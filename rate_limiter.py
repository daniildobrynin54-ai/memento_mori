"""Rate limiter для HTTP-запросов."""

import time
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)


class RateLimitedSession:
    """Обёртка над requests.Session с rate limiting."""
    
    def __init__(
        self,
        session: requests.Session,
        min_interval: float = 0.5
    ):
        """
        Args:
            session: базовая сессия requests
            min_interval: минимальный интервал между запросами (сек)
        """
        self._session = session
        self._min_interval = min_interval
        self._last_request_time: Optional[float] = None
    
    def _wait_if_needed(self):
        """Ждёт, если нужно соблюсти интервал."""
        if self._last_request_time is None:
            return
        
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
            logger.debug(f"Rate limit: ожидание {sleep_time:.2f}s")
            time.sleep(sleep_time)
    
    def get(self, *args, **kwargs):
        """GET-запрос с rate limiting."""
        self._wait_if_needed()
        self._last_request_time = time.time()
        return self._session.get(*args, **kwargs)
    
    def post(self, *args, **kwargs):
        """POST-запрос с rate limiting."""
        self._wait_if_needed()
        self._last_request_time = time.time()
        return self._session.post(*args, **kwargs)
    
    def put(self, *args, **kwargs):
        """PUT-запрос с rate limiting."""
        self._wait_if_needed()
        self._last_request_time = time.time()
        return self._session.put(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        """DELETE-запрос с rate limiting."""
        self._wait_if_needed()
        self._last_request_time = time.time()
        return self._session.delete(*args, **kwargs)
    
    def __getattr__(self, name):
        """Проксирует остальные атрибуты к базовой сессии."""
        return getattr(self._session, name)
