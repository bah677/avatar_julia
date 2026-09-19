"""
Обработчик геолокации - преобразование координат в текст.
Адаптировано из старого проекта.
"""

import logging
from typing import Optional, Dict, Any

import aiohttp

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType

logger = logging.getLogger(__name__)


class LocationProcessor(BaseProcessor):
    """Обработка геолокации - получение адреса по координатам"""
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли сообщение геолокацией"""
        return file_info.get('file_type') == 'location'
    
    async def _reverse_geocode(self, lat: float, lon: float) -> Optional[str]:
        """
        Получает адрес по координатам через OpenStreetMap Nominatim.
        Бесплатно, без ключа.
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://nominatim.openstreetmap.org/reverse"
                params = {
                    'lat': lat,
                    'lon': lon,
                    'format': 'json',
                    'zoom': 18,
                    'addressdetails': 1
                }
                headers = {
                    'User-Agent': 'MironBot/1.0'
                }
                
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        address = data.get('display_name', '')
                        return address
                    else:
                        logger.warning(f"⚠️ Nominatim returned {response.status}")
        except Exception as e:
            logger.warning(f"⚠️ Reverse geocoding failed: {e}")
        return None
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Преобразует координаты в текст с адресом.
        """
        start_time = self._start_timer()
        
        try:
            lat = file_info.get('latitude')
            lon = file_info.get('longitude')
            
            if lat is None or lon is None:
                raise ValueError("No coordinates in location data")
            
            # Пытаемся получить адрес
            address = await self._reverse_geocode(lat, lon)
            
            processing_time = self._end_timer(start_time)
            
            # Формируем текст
            if address:
                text = f"[локация: {address}]"
                confidence = 0.9
                metadata = {
                    'latitude': lat,
                    'longitude': lon,
                    'address': address,
                    'has_address': True
                }
            else:
                text = f"[локация: координаты {lat:.4f}, {lon:.4f}]"
                confidence = 0.7
                metadata = {
                    'latitude': lat,
                    'longitude': lon,
                    'address': None,
                    'has_address': False
                }
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.LOCATION,
                user_id=user_id,
                confidence=confidence,
                has_text=True,
                processing_time_ms=processing_time,
                metadata=metadata
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"📍 Локация обработана: {lat:.4f}, {lon:.4f}, адрес={'найден' if address else 'не найден'}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Location processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[локация (ошибка обработки)]",
                media_type=MediaType.LOCATION,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result