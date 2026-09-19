"""
Курсы ЦБ РФ → пересчёт суммы платежа в рубли для учёта (``payments.amount_rub``, ``exchange_rate``).

Не провайдерский FX для списания — только официальный дневной курс на дату, пригодную для отчётности.
Документ format: https://www.cbr.ru/development/DWS/
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

CBR_XML_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

# Одна выписка ЦБ на дату; intra-day перезапрос смыслен при сетевых сбоях.
_CACHE_TTL_SEC = 3600
_MAX_CACHE_DATES = 40
# На выходных/праздниках выгрузка может быть за последний рабочий день.
_RATE_LOOKBACK_DAYS = 7
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=10)


def resolve_payment_datetime_for_rates(payment: Optional[Dict[str, Any]]) -> datetime:
    """
    Опорный момент для даты курса: завершение платежа → обновление строки → создание → сейчас.

    Для pending (до finalize) обычно есть ``created_at`` — день выставления счёта.
    """
    if not payment:
        return datetime.now()
    for key in ("completed_at", "updated_at", "created_at"):
        v = payment.get(key)
        if isinstance(v, datetime):
            return v
    return datetime.now()


class CurrencyConverterService:
    """Котировки ЦБ РФ (XML daily), конвертация в RUB для строк заказа/платежа."""

    def __init__(self) -> None:
        #: iso date -> (monotonic_ts, {CharCode: rate_to_1_unit_in_rub})
        self._rates_cache: OrderedDict[str, tuple[float, Dict[str, float]]] = OrderedDict()
        logger.info("CurrencyConverterService (CBR FX) готов")

    async def _fetch_rates_from_cbr(self, target_date: date) -> Dict[str, float]:
        date_str = target_date.strftime("%d/%m/%Y")
        url = f"{CBR_XML_URL}?date_req={date_str}"
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error("CBR FX: HTTP %s for %s", response.status, target_date)
                        return {}
                    xml_text = await response.text()
        except aiohttp.ClientError as e:
            logger.error("CBR FX: request failed date=%s: %s", target_date, e)
            return {}
        except asyncio.TimeoutError:
            logger.error("CBR FX: timeout date=%s", target_date)
            return {}

        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as e:
            logger.error("CBR FX: bad XML date=%s: %s", target_date, e)
            return {}

        rates: Dict[str, float] = {}
        for valute in root.findall("Valute"):
            cc_el = valute.find("CharCode")
            val_el = valute.find("Value")
            nom_el = valute.find("Nominal")
            if cc_el is None or not cc_el.text:
                continue
            if val_el is None or not val_el.text or nom_el is None or not nom_el.text:
                continue
            try:
                value = float(str(val_el.text).replace(",", "."))
                nominal = int(str(nom_el.text).strip())
                if nominal <= 0:
                    continue
                rates[cc_el.text.strip().upper()] = value / nominal
            except (TypeError, ValueError):
                continue

        rates["RUB"] = 1.0
        if len(rates) <= 1:
            logger.warning("CBR FX: empty or invalid sheet for date=%s", target_date)
            return {}

        logger.info("CBR FX: loaded %s currencies for %s", len(rates), target_date)
        return rates

    async def _get_rates_for_date(self, target_date: date) -> Dict[str, float]:
        key = target_date.isoformat()
        now_m = time.monotonic()
        if key in self._rates_cache:
            ts, rates = self._rates_cache[key]
            if rates and (now_m - ts) < _CACHE_TTL_SEC:
                self._rates_cache.move_to_end(key)
                return rates

        rates = await self._fetch_rates_from_cbr(target_date)
        if rates:
            self._rates_cache[key] = (now_m, rates)
            self._rates_cache.move_to_end(key)
            while len(self._rates_cache) > _MAX_CACHE_DATES:
                self._rates_cache.popitem(last=False)
        return rates

    async def get_rate_to_rub(self, currency_code: str, target_date: date) -> Optional[float]:
        """
        Курс: сколько рублей за 1 единицу инвалюты (по методике XML ЦБ для CharCode).

        Если на запрошенный день нет строки для валюты, пробуем предыдущие дни (_RATE_LOOKBACK_DAYS).
        """
        code = (currency_code or "").strip().upper()
        if code == "RUB":
            return 1.0

        request_day = target_date
        for offset in range(_RATE_LOOKBACK_DAYS + 1):
            day = target_date - timedelta(days=offset)
            rates = await self._get_rates_for_date(day)
            if not rates:
                continue
            rate = rates.get(code)
            if rate is not None and rate > 0:
                if offset > 0:
                    logger.info(
                        "CBR FX: %s использован курс от %s (запрос на %s)",
                        code,
                        day,
                        request_day,
                    )
                return rate

        logger.error(
            "CBR FX: нет курса для %s около даты %s (lookback=%s)",
            code,
            request_day,
            _RATE_LOOKBACK_DAYS,
        )
        return None

    async def convert_payment_amount(
        self,
        amount: float,
        currency: str,
        payment_date: datetime,
    ) -> Optional[float]:
        """
        Сумма в валюте заказа → рубли по курсу ЦБ на календарную дату ``payment_date``.

        При ошибке или неизвестной валюте возвращает ``None`` (не подставляет сумму как RUB).
        """
        if amount < 0:
            logger.warning("CBR FX: отрицательная сумма %.6g — конвертация отклонена", amount)
            return None

        currency_u = (currency or "").strip().upper()
        if currency_u == "RUB":
            return float(amount)

        rate = await self.get_rate_to_rub(currency_u, payment_date.date())
        if rate is None:
            return None

        rub_amount = float(amount) * rate
        logger.info(
            "💰 FX: %.6g %s → %.2f RUB (rate=%.6g, дата=%s)",
            amount,
            currency_u,
            rub_amount,
            rate,
            payment_date.date(),
        )
        return rub_amount
