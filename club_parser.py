"""Парсер страницы клуба для проверки членства."""

import logging
import re
from typing import Optional, Tuple
from bs4 import BeautifulSoup
import requests

from config import BASE_URL, CLUB_PAGE_PATH

logger = logging.getLogger(__name__)


def check_club_membership(
    session: requests.Session,
    mangabuff_id: int
) -> Tuple[bool, str]:
    """
    Проверяет членство в клубе на сайте.
    
    Args:
        session: авторизованная сессия
        mangabuff_id: ID пользователя на MangaBuff
    
    Returns:
        (is_member, nickname) - состоит ли в клубе и его ник
    """
    try:
        url = f"{BASE_URL}{CLUB_PAGE_PATH}"
        response = session.get(url)
        
        if response.status_code != 200:
            logger.error(f"Ошибка загрузки страницы клуба: {response.status_code}")
            return False, ""
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем всех участников клуба (включая скрытые элементы)
        members = soup.select(".club__member")
        
        for member in members:
            # Ищем ссылку на профиль
            link = member.select_one("a.club__member-image, a[href*='/users/']")
            if link:
                href = link.get("href", "")
                match = re.search(r'/users/(\d+)', href)
                
                if match and int(match.group(1)) == mangabuff_id:
                    # Нашли пользователя, извлекаем ник
                    nick_elem = member.select_one("a.club__member-name, .club__member-name")
                    if nick_elem:
                        nick = nick_elem.get_text(strip=True)
                        logger.info(f"✅ Пользователь {mangabuff_id} найден в клубе: {nick}")
                        return True, nick
                    else:
                        logger.info(f"✅ Пользователь {mangabuff_id} найден в клубе")
                        return True, ""
        
        logger.info(f"❌ Пользователь {mangabuff_id} не найден в клубе")
        return False, ""
        
    except Exception as e:
        logger.error(f"Ошибка проверки членства в клубе: {e}", exc_info=True)
        return False, ""
