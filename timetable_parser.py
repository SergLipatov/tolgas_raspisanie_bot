import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import logging
import time

logger = logging.getLogger(__name__)

def get_groups_data():
    """
    Получает список всех доступных групп с сайта

    :return: список групп в формате [{'name': 'Имя группы', 'rel': 'значение rel', 'value': 'ID группы'}]
    """
    url = "https://www.tolgas.ru/services/raspisanie/?id=0"

    # Заголовки для имитации браузера
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
    }

    try:
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            logger.error(f"Failed to get groups: HTTP {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        select_element = soup.find('select', {'id': 'vr'})

        if not select_element:
            logger.error("Select element 'vr' not found on page")
            return []

        options = select_element.find_all('option')

        groups_data = []

        for option in options:
            if option.has_attr('rel') and option.has_attr('value'):
                group_info = {
                    'name': option.text.strip(),
                    'rel': option['rel'],
                    'value': option['value'],
                }
                groups_data.append(group_info)

        return groups_data

    except Exception as e:
        logger.error(f"Error fetching groups data: {e}")
        return []

def parse_timetable(group_id, start_date=None, end_date=None):
    """
    Парсит расписание для указанной группы на указанный период с использованием POST запроса

    :param group_id: ID группы (параметр vr)
    :param start_date: начальная дата в формате DD.MM.YYYY (по умолчанию - текущая дата)
    :param end_date: конечная дата в формате DD.MM.YYYY (по умолчанию - +14 дней)
    :return: список занятий
    """
    try:
        if start_date is None:
            start_date = datetime.now().strftime('%d.%m.%Y')

        if end_date is None:
            end_date = (datetime.now() + timedelta(days=14)).strftime('%d.%m.%Y')

        url = "https://www.tolgas.ru/services/raspisanie/"

        # Данные для POST запроса
        data = {
            'id': 0,
            'rel': 0,
            'grp': 0,
            'prep': 0,
            'audi': 0,
            'vr': group_id,
            'from': start_date,
            'to': end_date,
            'submit_button': 'Показать'
        }

        # Заголовки для имитации браузера
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.tolgas.ru',
            'Referer': 'https://www.tolgas.ru/services/raspisanie/',
        }

        logger.info(f"Sending POST request to {url} for group {group_id} from {start_date} to {end_date}")

        # Отправляем POST запрос
        response = requests.post(url, data=data, headers=headers)

        logger.info(f"Response status code: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"Failed to get timetable: HTTP {response.status_code}")
            logger.error(f"Response content: {response.text[:500]}...")
            return []

        # Проверка наличия таблицы расписания в ответе
        soup = BeautifulSoup(response.text, 'html.parser')

        # Проверяем наличие элементов расписания
        date_elements = soup.find_all('div', class_='timetable-frame-current-date__text--2')

        if not date_elements:
            logger.warning(f"Timetable elements not found in response for group ID {group_id}")
            # Логируем часть ответа для диагностики
            logger.debug(f"Response content preview: {response.text[:1000]}...")
            return []

        lessons = []
        current_date = None

        # Парсинг дат и занятий
        for date_elem in date_elements:
            date_text = date_elem.text.strip()
            if re.match(r'\d{2}\.\d{2}\.\d{4}', date_text):
                current_date = date_text

                # Находим родительский блок с датой
                date_container = date_elem.find_parent('div', class_='timetable-frame__row--2')

                # Находим все блоки с занятиями, которые идут после даты
                next_element = date_container.find_next_sibling('div', class_='timetable-frame__row--3')

                while next_element and 'timetable-frame__row--3' in next_element.get('class', []):
                    # Обрабатываем блоки с занятиями
                    for lesson_item in next_element.select('.timetable-frame-item'):
                        # Номер пары
                        number_elem = lesson_item.select_one('.timetable-frame-item__number')
                        if not number_elem:
                            continue
                        number = number_elem.text.strip()

                        # Время начала и окончания
                        time_spans = lesson_item.select('.timetable-frame-item__time span')
                        time_start = time_spans[0].text.strip() if len(time_spans) > 0 else ""
                        time_end = time_spans[1].text.strip() if len(time_spans) > 1 else ""

                        # Название предмета
                        subject_elem = lesson_item.select_one('.timetable-frame-item__title')
                        if not subject_elem:
                            continue
                        subject = subject_elem.text.strip()

                        # Тип занятия
                        lesson_type_elem = lesson_item.select_one('.timetable-frame-item__type')
                        lesson_type = lesson_type_elem.text.strip() if lesson_type_elem else ""

                        # Аудитория
                        audience_elem = None
                        for p_elem in lesson_item.select('.timetable-frame-item__text--1 p'):
                            if 'Аудитория:' in p_elem.text:
                                audience_elem = p_elem
                                break
                        audience = audience_elem.text.replace('Аудитория:', '').strip() if audience_elem else ""

                        # Преподаватель
                        teacher_elem = None
                        for p_elem in lesson_item.select('.timetable-frame-item__text--1 p'):
                            if 'Преподаватель:' in p_elem.text:
                                teacher_elem = p_elem
                                break
                        teacher = teacher_elem.text.replace('Преподаватель:', '').strip() if teacher_elem else ""

                        # Группа
                        group_elem = lesson_item.select_one('.timetable-frame-item__text--2 p')
                        group = group_elem.text.replace('Для групп:', '').strip() if group_elem else ""

                        lesson_data = {
                            'date': current_date,
                            'number': number,
                            'time_start': time_start,
                            'time_end': time_end,
                            'subject': subject,
                            'type': lesson_type,
                            'audience': audience,
                            'teacher': teacher,
                            'group': group
                        }

                        lessons.append(lesson_data)

                    # Переходим к следующему блоку
                    next_element = next_element.find_next_sibling()

        logger.info(f"Found {len(lessons)} lessons for group ID {group_id}")
        return lessons

    except Exception as e:
        logger.error(f"Error parsing timetable: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []
