"""
Парсер дополнительной информации о карте:
- Название карты (data-name со страницы /cards/{id}/users)
- Ники владельцев (data-name со страниц /users/{id})
"""

import logging
from typing import Optional, List, Tuple

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_DELAY = 3


def get_card_name(session: requests.Session, card_id: int) -> str:
    """
    Получает название карты со страницы /cards/{card_id}/users.

    Ищет: <div class="card-show" data-name="Гарнизон" ...>

    Args:
        session: авторизованная сессия
        card_id: ID карты

    Returns:
        Название карты или запасное значение
    """
    url = f"{BASE_URL}/cards/{card_id}/users"

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code != 200:
                logger.warning(
                    f"Ошибка загрузки страницы карты {card_id}: "
                    f"{response.status_code} (попытка {attempt + 1})"
                )
                if attempt < MAX_RETRIES - 1:
                    import time; time.sleep(RETRY_DELAY)
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            # Ищем <div class="card-show" data-name="...">
            card_div = soup.find("div", class_="card-show")
            if card_div:
                name = card_div.get("data-name", "").strip()
                if name:
                    logger.debug(f"Название карты {card_id}: {name}")
                    return name

            logger.warning(f"Не удалось найти data-name для карты {card_id}")
            return f"Карта #{card_id}"

        except Exception as e:
            logger.error(f"Ошибка при получении названия карты {card_id}: {e}")
            if attempt < MAX_RETRIES - 1:
                import time; time.sleep(RETRY_DELAY)

    return f"Карта #{card_id}"


def get_user_nickname(session: requests.Session, user_id: int) -> str:
    """
    Получает ник пользователя со страницы /users/{user_id}.

    Ищет: <div class="mobile-profile__name" data-name="Witch Plays" ...>

    Args:
        session: авторизованная сессия
        user_id: ID пользователя на MangaBuff

    Returns:
        Ник пользователя или запасное значение
    """
    url = f"{BASE_URL}/users/{user_id}"

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code != 200:
                logger.warning(
                    f"Ошибка загрузки профиля {user_id}: "
                    f"{response.status_code} (попытка {attempt + 1})"
                )
                if attempt < MAX_RETRIES - 1:
                    import time; time.sleep(RETRY_DELAY)
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            # Мобильный профиль: <div class="mobile-profile__name" data-name="...">
            name_div = soup.find("div", class_="mobile-profile__name")
            if name_div:
                nick = name_div.get("data-name", "").strip()
                if nick:
                    logger.debug(f"Ник пользователя {user_id}: {nick}")
                    return nick

            # Запасной вариант: десктопный профиль
            profile_div = soup.find("div", class_="profile__name")
            if profile_div:
                nick = profile_div.get_text(strip=True)
                if nick:
                    return nick

            logger.warning(f"Не удалось найти ник пользователя {user_id}")
            return f"User#{user_id}"

        except Exception as e:
            logger.error(f"Ошибка при получении ника пользователя {user_id}: {e}")
            if attempt < MAX_RETRIES - 1:
                import time; time.sleep(RETRY_DELAY)

    return f"User#{user_id}"


def get_owners_nicknames(
    session: requests.Session,
    owner_ids: List[int],
    max_owners: int = 10
) -> List[Tuple[int, str]]:
    """
    Получает ники для списка владельцев карты.

    Args:
        session: авторизованная сессия
        owner_ids: список ID пользователей
        max_owners: максимальное количество пользователей для запроса

    Returns:
        список (user_id, nickname)
    """
    result = []
    limited_ids = owner_ids[:max_owners]

    for user_id in limited_ids:
        nick = get_user_nickname(session, user_id)
        result.append((user_id, nick))

    if len(owner_ids) > max_owners:
        logger.info(
            f"Показаны первые {max_owners} из {len(owner_ids)} владельцев"
        )

    return result