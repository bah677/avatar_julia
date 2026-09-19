"""
Обработчик документов - извлечение текста из PDF, DOC, TXT.
Адаптировано из старого проекта.
"""

import os
import subprocess
import logging
from typing import Optional, Dict, Any

import aiofiles

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import SUPPORTED_DOCUMENT_TYPES, MEDIA_LIMITS

logger = logging.getLogger(__name__)


class DocumentProcessor(BaseProcessor):
    """Обработка документов - извлечение текста"""
    
    def __init__(self, user_storage):
        super().__init__(user_storage)
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл документом"""
        file_type = file_info.get('file_type')
        mime = file_info.get('mime_type', '')
        
        return file_type == 'document' or mime in SUPPORTED_DOCUMENT_TYPES
    
    async def _extract_text_from_txt(self, file_path: str) -> Optional[str]:
        """Извлекает текст из TXT файла"""
        try:
            # Пробуем UTF-8
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                text = await f.read()
                return text
        except UnicodeDecodeError:
            try:
                # Пробуем Windows-1251
                async with aiofiles.open(file_path, 'r', encoding='cp1251') as f:
                    text = await f.read()
                    return text
            except UnicodeDecodeError:
                # Пробуем Latin-1 (всегда работает)
                async with aiofiles.open(file_path, 'r', encoding='latin-1') as f:
                    text = await f.read()
                    return text
    
    async def _extract_text_from_pdf(self, file_path: str) -> Optional[str]:
        """Извлекает текст из PDF через pdftotext"""
        try:
            # Проверяем наличие pdftotext
            result = subprocess.run(
                ['which', 'pdftotext'],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                logger.warning("⚠️ pdftotext not installed, skipping PDF extraction")
                return None
            
            # Извлекаем текст
            result = subprocess.run(
                ['pdftotext', file_path, '-'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception as e:
            logger.error(f"❌ PDF extraction error: {e}")
            return None
    
    async def _extract_text_from_doc(self, file_path: str) -> Optional[str]:
        """Извлекает текст из DOC/DOCX"""
        try:
            # Пробуем catdoc (для старых .doc)
            result = subprocess.run(
                ['which', 'catdoc'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                doc_result = subprocess.run(
                    ['catdoc', file_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if doc_result.returncode == 0:
                    return doc_result.stdout
            
            # Пробуем textutil (macOS) для .docx
            result = subprocess.run(
                ['which', 'textutil'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # Конвертируем в txt
                txt_path = file_path + '.txt'
                subprocess.run(
                    ['textutil', '-convert', 'txt', '-output', txt_path, file_path],
                    timeout=30
                )
                if os.path.exists(txt_path):
                    async with aiofiles.open(txt_path, 'r', encoding='utf-8') as f:
                        text = await f.read()
                    os.unlink(txt_path)
                    return text
            
            return None
        except Exception as e:
            logger.error(f"❌ DOC extraction error: {e}")
            return None
    
    async def _extract_text_from_rtf(self, file_path: str) -> Optional[str]:
        """Извлекает текст из RTF"""
        try:
            result = subprocess.run(
                ['unrtf', '--text', file_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception as e:
            logger.error(f"❌ RTF extraction error: {e}")
            return None
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Извлекает текст из документа.
        """
        start_time = self._start_timer()
        
        try:
            if not file_path:
                raise ValueError("No file path provided")
            
            mime = file_info.get('mime_type', '')
            file_name = file_info.get('file_name', 'документ')
            extracted_text = None
            method = 'unknown'
            
            # Выбираем метод извлечения по типу
            if mime in ['text/plain', 'text/markdown'] or file_name.endswith(('.txt', '.md')):
                extracted_text = await self._extract_text_from_txt(file_path)
                method = 'txt'
            elif 'pdf' in mime or file_name.endswith('.pdf'):
                extracted_text = await self._extract_text_from_pdf(file_path)
                method = 'pdftotext'
            elif 'word' in mime or 'msword' in mime or file_name.endswith(('.doc', '.docx')):
                extracted_text = await self._extract_text_from_doc(file_path)
                method = 'catdoc/textutil'
            elif 'rtf' in mime or file_name.endswith('.rtf'):
                extracted_text = await self._extract_text_from_rtf(file_path)
                method = 'unrtf'
            elif 'opendocument' in mime or file_name.endswith('.odt'):
                # ODT - можно через unzip и чтение content.xml, пока пропускаем
                logger.warning(f"⚠️ ODT extraction not implemented yet")
                method = 'not_implemented'
            
            processing_time = self._end_timer(start_time)
            
            # Формируем текст для сообщения
            if extracted_text and extracted_text.strip():
                # Обрезаем до лимита (уже будет обрезано в processor.py)
                text = f"[документ {file_name}: {extracted_text}]"
                confidence = 0.8
                has_text = True
            else:
                text = f"[документ {file_name} (текст не найден)]"
                confidence = 0.0
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.DOCUMENT,
                user_id=user_id,
                confidence=confidence,
                has_text=has_text,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                mime_type=mime,
                metadata={
                    'file_name': file_name,
                    'extraction_method': method,
                    'extracted_length': len(extracted_text) if extracted_text else 0
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"📄 Документ {file_name} обработан, метод={method}, текст найден={has_text}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Document processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[документ (ошибка обработки)]",
                media_type=MediaType.DOCUMENT,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                mime_type=file_info.get('mime_type'),
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result