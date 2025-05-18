#!/usr/bin/env python3
import logging
import asyncio
from bot import main
from timetable_parser import get_groups_data, parse_timetable
from database import Database

if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        filename='timetable_bot.log',  # Добавляем логирование в файл
        filemode='a'  # Дописываем логи, а не перезаписываем
    )
    logger = logging.getLogger(__name__)

    # Добавляем вывод логов в консоль
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)

    logger.info("Инициализация бота для расписания занятий")
    logger.info("Инициализация базы данных...")
    db = Database()

    # Проверка незавершенных обновлений
    incomplete_updates = db.check_incomplete_updates()
    if incomplete_updates > 0:
        logger.warning(f"Found {incomplete_updates} incomplete updates from previous run")

    logger.info("Запуск бота...")
    main()
