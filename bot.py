import logging
import asyncio
import os
import sys
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters
)
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

from database import Database
from timetable_parser import parse_timetable, get_groups_data

# Загрузка переменных окружения
load_dotenv()

# Получение токена из переменных окружения
TOKEN = os.getenv('TOKEN')

# Проверка наличия токена
if not TOKEN:
    print("Ошибка: Токен не найден. Убедитесь, что файл .env содержит TOKEN=ваш_токен")
    sys.exit(1)

# Константы для конечного автомата
SELECTING_GROUP, SELECTING_ACTION, ENTERING_GROUP_NAME, SELECTING_NOTIFICATION_TIME = range(4)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
db = Database()

# Временная зона для корректного отображения времени
TIMEZONE = pytz.timezone('Europe/Moscow')

async def update_groups_list():
    """Обновляет список групп с учетом времени последнего обновления"""
    # Проверяем, нужно ли обновлять список групп
    if not db.is_update_needed('groups_list'):
        logger.info("Пропуск обновления списка групп - еще не время")
        return

    try:
        # Отмечаем начало обновления
        db.start_update('groups_list')

        logger.info("Обновление списка групп")
        groups = get_groups_data()

        if groups:
            count = 0
            for group in groups:
                if db.add_group(group['name'], group['value']):
                    count += 1

            logger.info(f"Добавлено/обновлено {count} групп из {len(groups)}")

            # Запланировать следующее обновление через 7 дней (списки групп меняются редко)
            next_update = datetime.now() + timedelta(days=7)
            db.complete_update('groups_list', None, next_update, 'completed')
        else:
            logger.warning("Не удалось получить список групп")

            # Если не удалось получить данные, пробуем через час
            next_update = datetime.now() + timedelta(hours=1)
            db.complete_update('groups_list', None, next_update, 'failed')
    except Exception as e:
        logger.error(f"Ошибка при обновлении списка групп: {e}")
        db.complete_update('groups_list', None, datetime.now() + timedelta(hours=1), 'error')

async def update_timetable_for_all_groups():
    """Обновляет расписание для всех групп с учетом времени последнего обновления"""
    groups = db.get_all_groups()

    for _, group_name, group_id in groups:
        try:
            # Проверяем, нужно ли обновлять расписание для этой группы
            if not db.is_update_needed('timetable', group_id):
                logger.info(f"Пропуск обновления для группы {group_name} (ID: {group_id}) - еще не время")
                continue

            # Отмечаем начало обновления
            db.start_update('timetable', group_id)

            logger.info(f"Обновление расписания для группы {group_name} (ID: {group_id})")
            start_date = datetime.now().strftime('%d.%m.%Y')
            end_date = (datetime.now() + timedelta(days=30)).strftime('%d.%m.%Y')

            lessons = parse_timetable(group_id, start_date, end_date)

            if lessons:
                db.save_timetable(group_id, lessons)
                logger.info(f"Загружено {len(lessons)} занятий для группы {group_name}")

                # Запланировать следующее обновление через 24 часа
                next_update = datetime.now() + timedelta(hours=24)
                db.complete_update('timetable', group_id, next_update, 'completed')
            else:
                logger.warning(f"Не удалось получить расписание для группы {group_name}")

                # Если не удалось получить данные, пробуем через час
                next_update = datetime.now() + timedelta(hours=1)
                db.complete_update('timetable', group_id, next_update, 'failed')

            # Добавляем задержку между запросами
            await asyncio.sleep(1)  # 1 секунда задержки
        except Exception as e:
            logger.error(f"Ошибка при обновлении расписания для группы {group_name}: {e}")
            db.complete_update('timetable', group_id, datetime.now() + timedelta(hours=1), 'error')

    logger.info("Обновление расписания завершено")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает команду /start"""
    user = update.effective_user

    # Добавляем пользователя в базу данных
    db.add_user(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )

    keyboard = [
        [InlineKeyboardButton("Подписаться на расписание", callback_data='subscribe')],
        [InlineKeyboardButton("Мои подписки", callback_data='my_subscriptions')],
        [InlineKeyboardButton("Ближайшие занятия", callback_data='upcoming_lessons')],
        [InlineKeyboardButton("Расписание на сегодня", callback_data='today')],
        [InlineKeyboardButton("Расписание на завтра", callback_data='tomorrow')],
    ]

    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот для получения расписания занятий.\n\n"
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return SELECTING_ACTION

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатия на кнопки"""
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == 'subscribe':
        # Просим пользователя ввести название или часть названия группы
        await query.edit_message_text(
            "Введите название или часть названия группы (например, 'БОЗИ', 'оз23'):\n\n"
            "Поиск не чувствителен к регистру.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
        )

        return ENTERING_GROUP_NAME

    elif action == 'my_subscriptions':
        # Получаем список подписок пользователя
        subscriptions = db.get_user_subscriptions(update.effective_user.id)

        if not subscriptions:
            await query.edit_message_text(
                "У вас нет активных подписок. Используйте команду '/start' чтобы подписаться на расписание группы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
            return SELECTING_ACTION

        keyboard = []

        for group_id, group_name, group_external_id in subscriptions:
            keyboard.append([
                InlineKeyboardButton(
                    f"{group_name}",
                    callback_data=f"view_subscription_{group_external_id}"
                ),
                InlineKeyboardButton(
                    "❌ Отписаться",
                    callback_data=f"unsubscribe_{group_external_id}"
                )
            ])

        keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_main')])

        await query.edit_message_text(
            "Ваши подписки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action == 'upcoming_lessons':
        # Получаем список подписок пользователя
        subscriptions = db.get_user_subscriptions(update.effective_user.id)

        if not subscriptions:
            await query.edit_message_text(
                "У вас нет активных подписок. Используйте команду '/start' чтобы подписаться на расписание группы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
            return SELECTING_ACTION

        message = "Ближайшие занятия (в течение 24 часов):\n\n"
        has_lessons = False

        for _, group_name, group_external_id in subscriptions:
            upcoming_lessons = db.get_upcoming_lessons(group_external_id, hours=24)

            if upcoming_lessons:
                has_lessons = True
                message += f"*Группа {group_name}*:\n"

                for lesson in upcoming_lessons:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                    message += (
                        f"📅 {date} (пара {number})\n"
                        f"⏰ {time_start}-{time_end}\n"
                        f"📚 {subject} ({lesson_type})\n"
                        f"🏢 Аудитория: {audience}\n"
                        f"👨‍🏫 Преподаватель: {teacher}\n\n"
                    )

        if not has_lessons:
            message = "В ближайшие 24 часа занятий не найдено."

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )

        return SELECTING_ACTION

    elif action in ['today', 'tomorrow']:
        # Получаем список подписок пользователя
        subscriptions = db.get_user_subscriptions(update.effective_user.id)

        if not subscriptions:
            await query.edit_message_text(
                "У вас нет активных подписок. Используйте команду '/start' чтобы подписаться на расписание группы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
            return SELECTING_ACTION

        date = datetime.now()
        if action == 'tomorrow':
            date += timedelta(days=1)

        date_str = date.strftime('%d.%m.%Y')
        day_name = "Сегодня" if action == 'today' else "Завтра"

        message = f"Расписание на {day_name} ({date_str}):\n\n"
        has_lessons = False

        for _, group_name, group_external_id in subscriptions:
            lessons = db.get_timetable_for_group(group_external_id, date_str)

            if lessons:
                has_lessons = True
                message += f"*Группа {group_name}*:\n"

                for lesson in lessons:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                    message += (
                        f"📚 {subject} ({lesson_type})\n"
                        f"⏰ {time_start}-{time_end} (пара {number})\n"
                        f"🏢 Аудитория: {audience}\n"
                        f"👨‍🏫 Преподаватель: {teacher}\n\n"
                    )
            else:
                has_lessons = True
                message += f"*Группа {group_name}*: занятий нет\n\n"

        if not has_lessons:
            message = f"На {day_name.lower()} ({date_str}) занятий не найдено."

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )

        return SELECTING_ACTION

    elif action == 'back_to_main':
        # Возвращаемся в главное меню
        keyboard = [
            [InlineKeyboardButton("Подписаться на расписание", callback_data='subscribe')],
            [InlineKeyboardButton("Мои подписки", callback_data='my_subscriptions')],
            [InlineKeyboardButton("Ближайшие занятия", callback_data='upcoming_lessons')],
            [InlineKeyboardButton("Расписание на сегодня", callback_data='today')],
            [InlineKeyboardButton("Расписание на завтра", callback_data='tomorrow')],
        ]

        await query.edit_message_text(
            "Выберите действие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('group_'):
        # Пользователь выбрал группу для подписки
        group_id = action.split('_')[1]
        user_id = update.effective_user.id

        # Получаем database user_id
        db_user_id = db.add_user(
            user_id,
            update.effective_user.username,
            update.effective_user.first_name,
            update.effective_user.last_name
        )

        # Подписываем пользователя на группу
        result = db.subscribe_to_group(db_user_id, group_id)

        if result:
            # Обновляем расписание для выбранной группы
            start_date = datetime.now().strftime('%d.%m.%Y')
            end_date = (datetime.now() + timedelta(days=30)).strftime('%d.%m.%Y')

            try:
                # Отмечаем начало обновления
                db.start_update('timetable', group_id)

                lessons = parse_timetable(group_id, start_date, end_date)
                if lessons:
                    db.save_timetable(group_id, lessons)
                    # Отмечаем успешное завершение
                    next_update = datetime.now() + timedelta(hours=24)
                    db.complete_update('timetable', group_id, next_update, 'completed')
                else:
                    # Отмечаем неудачу
                    next_update = datetime.now() + timedelta(hours=1)
                    db.complete_update('timetable', group_id, next_update, 'failed')

                # Получаем название группы
                group_info = db.get_group_by_id(group_id)
                group_name = group_info[1] if group_info else "Unknown"

                await query.edit_message_text(
                    f"Вы успешно подписались на расписание группы {group_name}!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
                )
            except Exception as e:
                logger.error(f"Ошибка при обновлении расписания: {e}")
                # Отмечаем ошибку
                db.complete_update('timetable', group_id, datetime.now() + timedelta(hours=1), 'error')

                await query.edit_message_text(
                    "Произошла ошибка при обновлении расписания. Пожалуйста, попробуйте позже.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
                )
        else:
            await query.edit_message_text(
                "Вы уже подписаны на эту группу или произошла ошибка.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )

        return SELECTING_ACTION

    elif action.startswith('unsubscribe_'):
        # Пользователь хочет отписаться от группы
        group_id = action.split('_')[1]
        user_id = update.effective_user.id

        # Отписываем пользователя от группы
        result = db.unsubscribe_from_group(user_id, group_id)

        if result:
            await query.edit_message_text(
                "Вы успешно отписались от расписания.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
        else:
            await query.edit_message_text(
                "Произошла ошибка при отписке. Пожалуйста, попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )

        return SELECTING_ACTION

    elif action.startswith('view_subscription_'):
        # Пользователь хочет посмотреть расписание для конкретной подписки
        group_id = action.split('_')[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("На сегодня", callback_data=f"view_today_{group_id}")],
            [InlineKeyboardButton("На завтра", callback_data=f"view_tomorrow_{group_id}")],
            [InlineKeyboardButton("На неделю", callback_data=f"view_week_{group_id}")],
            [InlineKeyboardButton("Настройки уведомлений", callback_data=f"notification_settings_{group_id}")],
            [InlineKeyboardButton("Назад к подпискам", callback_data='my_subscriptions')],
            [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
        ]

        await query.edit_message_text(
            f"Расписание группы {group_name}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('view_today_') or action.startswith('view_tomorrow_') or action.startswith('view_week_'):
        parts = action.split('_')
        view_type = parts[1]
        group_id = parts[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if view_type == 'today':
            date = datetime.now()
            date_str = date.strftime('%d.%m.%Y')

            lessons = db.get_timetable_for_group(group_id, date_str)

            if not lessons:
                message = f"На сегодня ({date_str}) для группы {group_name} занятий не найдено."
            else:
                message = f"Расписание на сегодня ({date_str}) для группы {group_name}:\n\n"

                for lesson in lessons:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                    message += (
                        f"📚 {subject} ({lesson_type})\n"
                        f"⏰ {time_start}-{time_end} (пара {number})\n"
                        f"🏢 Аудитория: {audience}\n"
                        f"👨‍🏫 Преподаватель: {teacher}\n\n"
                    )

        elif view_type == 'tomorrow':
            date = datetime.now() + timedelta(days=1)
            date_str = date.strftime('%d.%m.%Y')

            lessons = db.get_timetable_for_group(group_id, date_str)

            if not lessons:
                message = f"На завтра ({date_str}) для группы {group_name} занятий не найдено."
            else:
                message = f"Расписание на завтра ({date_str}) для группы {group_name}:\n\n"

                for lesson in lessons:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                    message += (
                        f"📚 {subject} ({lesson_type})\n"
                        f"⏰ {time_start}-{time_end} (пара {number})\n"
                        f"🏢 Аудитория: {audience}\n"
                        f"👨‍🏫 Преподаватель: {teacher}\n\n"
                    )

        elif view_type == 'week':
            today = datetime.now()
            message = f"Расписание на неделю для группы {group_name}:\n\n"
            has_lessons = False

            for i in range(7):
                date = today + timedelta(days=i)
                date_str = date.strftime('%d.%m.%Y')
                day_name = date.strftime('%A').capitalize()

                # Переводим день недели на русский
                day_names = {
                    'Monday': 'Понедельник',
                    'Tuesday': 'Вторник',
                    'Wednesday': 'Среда',
                    'Thursday': 'Четверг',
                    'Friday': 'Пятница',
                    'Saturday': 'Суббота',
                    'Sunday': 'Воскресенье'
                }

                ru_day_name = day_names.get(day_name, day_name)

                lessons = db.get_timetable_for_group(group_id, date_str)

                if lessons:
                    has_lessons = True
                    message += f"*{ru_day_name} ({date_str})*:\n"

                    for lesson in lessons:
                        _, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                        message += (
                            f"📚 {subject} ({lesson_type})\n"
                            f"⏰ {time_start}-{time_end} (пара {number})\n"
                            f"🏢 Аудитория: {audience}\n"
                            f"👨‍🏫 Преподаватель: {teacher}\n\n"
                        )
                else:
                    message += f"*{ru_day_name} ({date_str})*: занятий нет\n\n"

            if not has_lessons:
                message = f"На ближайшую неделю для группы {group_name} занятий не найдено."

        # Если сообщение слишком длинное, разбиваем его
        if len(message) > 4096:
            parts = [message[i:i+4096] for i in range(0, len(message), 4096)]

            for i, part in enumerate(parts):
                if i == 0:
                    await query.edit_message_text(
                        part,
                        parse_mode='Markdown'
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=part,
                        parse_mode='Markdown'
                    )

            # Отправляем кнопки в последнем сообщении
            keyboard = [
                [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
                [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
            ]

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Используйте кнопки для навигации:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
                [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
            ]

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        return SELECTING_ACTION

    elif action.startswith('notification_settings_'):
        # Настройки уведомлений для группы
        group_id = action.split('_')[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("За 15 минут", callback_data=f"notify_15_{group_id}")],
            [InlineKeyboardButton("За 30 минут", callback_data=f"notify_30_{group_id}")],
            [InlineKeyboardButton("За 1 час", callback_data=f"notify_60_{group_id}")],
            [InlineKeyboardButton("За 2 часа", callback_data=f"notify_120_{group_id}")],
            [InlineKeyboardButton("Выключить уведомления", callback_data=f"notify_off_{group_id}")],
            [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
        ]

        await query.edit_message_text(
            f"Настройка уведомлений для группы {group_name}.\n\n"
            "Выберите, за сколько времени до начала занятия получать уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('notify_'):
        # Обработка настройки времени уведомления
        parts = action.split('_')
        setting = parts[1]
        group_id = parts[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if setting == 'off':
            # Отключаем уведомления
            db.toggle_notifications(update.effective_user.id, group_id, False)
            message = f"Уведомления для группы {group_name} отключены."
        else:
            # Устанавливаем время уведомления
            minutes = int(setting)
            db.update_notification_settings(update.effective_user.id, group_id, minutes)
            db.toggle_notifications(update.effective_user.id, group_id, True)

            time_text = ""
            if minutes == 15 or minutes == 30:
                time_text = f"за {minutes} минут"
            else:
                hours = minutes // 60
                time_text = f"за {hours} час{'а' if hours > 1 else ''}"

            message = f"Настройки уведомлений обновлены. Вы будете получать уведомления {time_text} до начала занятия."

        keyboard = [
            [InlineKeyboardButton("Назад к настройкам", callback_data=f"notification_settings_{group_id}")],
            [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
            [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
        ]

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    return SELECTING_ACTION

async def handle_group_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод названия группы"""
    user_input = update.message.text.strip().lower()

    # Если введено слишком короткое название
    if len(user_input) < 2:
        await update.message.reply_text(
            "Введите не менее 2 символов для поиска группы.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
        )
        return ENTERING_GROUP_NAME

    # Получаем список всех групп и добавляем их в базу
    try:
        # Обновляем список групп, если нужно
        await update_groups_list()
    except Exception as e:
        logger.error(f"Error updating groups before search: {e}")

    # Ищем группы по введенному названию
    matching_groups = db.search_groups_by_name(user_input)

    if not matching_groups:
        await update.message.reply_text(
            f"Группы, содержащие '{user_input}', не найдены. Пожалуйста, попробуйте другой запрос.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад в главное меню", callback_data='back_to_main')]])
        )
        return ENTERING_GROUP_NAME

    # Если найдено слишком много групп, предложим уточнить запрос
    if len(matching_groups) > 30:
        await update.message.reply_text(
            f"Найдено {len(matching_groups)} групп. Пожалуйста, уточните запрос для уменьшения количества результатов.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад в главное меню", callback_data='back_to_main')]])
        )
        return ENTERING_GROUP_NAME

    # Создаем клавиатуру с найденными группами
    keyboard = []
    for _, group_name, external_id in matching_groups:
        keyboard.append([InlineKeyboardButton(group_name, callback_data=f"group_{external_id}")])

    keyboard.append([InlineKeyboardButton("Назад в главное меню", callback_data='back_to_main')])

    await update.message.reply_text(
        f"Найдено {len(matching_groups)} групп. Выберите нужную группу:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return SELECTING_GROUP

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью, когда пользователь вводит /help"""
    help_text = (
        "Список доступных команд:\n\n"
        "/start - Запустить бота и показать главное меню\n"
        "/search [название] - Быстрый поиск группы по названию\n"
        "/today - Расписание на сегодня для ваших подписок\n"
        "/tomorrow - Расписание на завтра для ваших подписок\n"
        "/week - Расписание на неделю для ваших подписок\n"
        "/subscriptions - Управление подписками\n"
        "/help - Показать эту справку\n\n"
        "С помощью этого бота вы можете:\n"
        "- Подписаться на расписание выбранных групп\n"
        "- Получать уведомления о предстоящих занятиях\n"
        "- Просматривать расписание на сегодня, завтра или всю неделю\n"
    )
    await update.message.reply_text(help_text)

async def quick_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Быстрый поиск группы по аргументам команды"""
    if not context.args:
        await update.message.reply_text(
            "Пожалуйста, укажите название или часть названия группы.\n"
            "Пример: /search БОЗИ"
        )
        return ConversationHandler.END

    search_query = ' '.join(context.args).strip().lower()

    if len(search_query) < 2:
        await update.message.reply_text(
            "Введите не менее 2 символов для поиска группы."
        )
        return ConversationHandler.END

    # Обновляем список групп
    try:
        await update_groups_list()
    except Exception as e:
        logger.error(f"Error updating groups before search: {e}")

    # Ищем группы
    matching_groups = db.search_groups_by_name(search_query)

    if not matching_groups:
        await update.message.reply_text(
            f"Группы, содержащие '{search_query}', не найдены. Пожалуйста, попробуйте другой запрос."
        )
        return ConversationHandler.END

    # Если найдено слишком много групп
    if len(matching_groups) > 30:
        await update.message.reply_text(
            f"Найдено {len(matching_groups)} групп. Пожалуйста, уточните запрос для уменьшения количества результатов."
        )
        return ConversationHandler.END

    # Создаем клавиатуру с найденными группами
    keyboard = []
    for _, group_name, external_id in matching_groups:
        keyboard.append([InlineKeyboardButton(group_name, callback_data=f"group_{external_id}")])

    keyboard.append([InlineKeyboardButton("Отмена", callback_data='back_to_main')])

    await update.message.reply_text(
        f"Найдено {len(matching_groups)} групп. Выберите нужную группу:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return SELECTING_GROUP

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на сегодня"""
    user_id = update.effective_user.id

    # Получаем список подписок пользователя
    subscriptions = db.get_user_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "У вас нет активных подписок. Используйте команду /start чтобы подписаться на расписание группы."
        )
        return

    date = datetime.now()
    date_str = date.strftime('%d.%m.%Y')

    message = f"Расписание на сегодня ({date_str}):\n\n"
    has_lessons = False

    for _, group_name, group_external_id in subscriptions:
        lessons = db.get_timetable_for_group(group_external_id, date_str)

        if lessons:
            has_lessons = True
            message += f"*Группа {group_name}*:\n"

            for lesson in lessons:
                date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                message += (
                    f"📚 {subject} ({lesson_type})\n"
                    f"⏰ {time_start}-{time_end} (пара {number})\n"
                    f"🏢 Аудитория: {audience}\n"
                    f"👨‍🏫 Преподаватель: {teacher}\n\n"
                )
        else:
            has_lessons = True
            message += f"*Группа {group_name}*: занятий нет\n\n"

    if not has_lessons:
        message = f"На сегодня ({date_str}) занятий не найдено."

    await update.message.reply_text(
        message,
        parse_mode='Markdown'
    )

async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на завтра"""
    user_id = update.effective_user.id

    # Получаем список подписок пользователя
    subscriptions = db.get_user_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "У вас нет активных подписок. Используйте команду /start чтобы подписаться на расписание группы."
        )
        return

    date = datetime.now() + timedelta(days=1)
    date_str = date.strftime('%d.%m.%Y')

    message = f"Расписание на завтра ({date_str}):\n\n"
    has_lessons = False

    for _, group_name, group_external_id in subscriptions:
        lessons = db.get_timetable_for_group(group_external_id, date_str)

        if lessons:
            has_lessons = True
            message += f"*Группа {group_name}*:\n"

            for lesson in lessons:
                date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                message += (
                    f"📚 {subject} ({lesson_type})\n"
                    f"⏰ {time_start}-{time_end} (пара {number})\n"
                    f"🏢 Аудитория: {audience}\n"
                    f"👨‍🏫 Преподаватель: {teacher}\n\n"
                )
        else:
            has_lessons = True
            message += f"*Группа {group_name}*: занятий нет\n\n"

    if not has_lessons:
        message = f"На завтра ({date_str}) занятий не найдено."

    await update.message.reply_text(
        message,
        parse_mode='Markdown'
    )

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на неделю"""
    user_id = update.effective_user.id

    # Получаем список подписок пользователя
    subscriptions = db.get_user_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "У вас нет активных подписок. Используйте команду /start чтобы подписаться на расписание группы."
        )
        return

    # Отправляем сообщение для каждой группы отдельно, чтобы избежать превышения лимита
    for _, group_name, group_external_id in subscriptions:
        today = datetime.now()
        message = f"Расписание на неделю для группы *{group_name}*:\n\n"
        has_lessons = False

        for i in range(7):
            date = today + timedelta(days=i)
            date_str = date.strftime('%d.%m.%Y')
            day_name = date.strftime('%A').capitalize()

            # Переводим день недели на русский
            day_names = {
                'Monday': 'Понедельник',
                'Tuesday': 'Вторник',
                'Wednesday': 'Среда',
                'Thursday': 'Четверг',
                'Friday': 'Пятница',
                'Saturday': 'Суббота',
                'Sunday': 'Воскресенье'
            }

            ru_day_name = day_names.get(day_name, day_name)

            lessons = db.get_timetable_for_group(group_external_id, date_str)

            if lessons:
                has_lessons = True
                message += f"*{ru_day_name} ({date_str})*:\n"

                for lesson in lessons:
                    _, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                    message += (
                        f"📚 {subject} ({lesson_type})\n"
                        f"⏰ {time_start}-{time_end} (пара {number})\n"
                        f"🏢 Аудитория: {audience}\n"
                        f"👨‍🏫 Преподаватель: {teacher}\n\n"
                    )
            else:
                message += f"*{ru_day_name} ({date_str})*: занятий нет\n\n"

        if not has_lessons:
            message = f"На ближайшую неделю для группы *{group_name}* занятий не найдено."

        # Разбиваем сообщение, если оно слишком длинное
        if len(message) > 4096:
            parts = [message[i:i+4096] for i in range(0, len(message), 4096)]

            for part in parts:
                await update.message.reply_text(
                    part,
                    parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                message,
                parse_mode='Markdown'
            )

async def subscriptions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список подписок"""
    user_id = update.effective_user.id

    # Получаем список подписок пользователя
    subscriptions = db.get_user_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "У вас нет активных подписок. Используйте команду /start чтобы подписаться на расписание группы."
        )
        return ConversationHandler.END

    keyboard = []

    for group_id, group_name, group_external_id in subscriptions:
        keyboard.append([
            InlineKeyboardButton(
                f"{group_name}",
                callback_data=f"view_subscription_{group_external_id}"
            ),
            InlineKeyboardButton(
                "❌ Отписаться",
                callback_data=f"unsubscribe_{group_external_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_main')])

    await update.message.reply_text(
        "Ваши подписки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return SELECTING_ACTION

async def check_upcoming_lessons(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет ближайшие занятия и отправляет уведомления"""
    try:
        # Получаем все группы
        groups = db.get_all_groups()

        for _, group_name, group_id in groups:
            # Получаем занятия в ближайшие 2 часа
            upcoming_lessons = db.get_upcoming_lessons(group_id, hours=2)

            for lesson in upcoming_lessons:
                date_str, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                # Получаем время начала занятия
                try:
                    lesson_datetime = datetime.strptime(f"{date_str} {time_start}", '%d.%m.%Y %H:%M')
                    now = datetime.now()

                    # Получаем пользователей для уведомления
                    users_to_notify = db.get_users_to_notify(group_id, lesson_datetime)

                    for telegram_id, notify_before_minutes in users_to_notify:
                        # Проверяем, нужно ли отправлять уведомление
                        notification_time = lesson_datetime - timedelta(minutes=notify_before_minutes)

                        # Если время для уведомления наступило (с точностью до 5 минут)
                        time_diff_minutes = (notification_time - now).total_seconds() / 60

                        if 0 <= time_diff_minutes <= 5:
                            # Отправляем уведомление
                            time_text = ""
                            if notify_before_minutes < 60:
                                time_text = f"{notify_before_minutes} мин."
                            else:
                                hours = notify_before_minutes // 60
                                mins = notify_before_minutes % 60
                                time_text = f"{hours} ч."
                                if mins > 0:
                                    time_text += f" {mins} мин."

                            message = (
                                f"⚠️ *Напоминание о занятии* ⚠️\n\n"
                                f"*Группа:* {group_name}\n"
                                f"*Предмет:* {subject} ({lesson_type})\n"
                                f"*Когда:* {date_str}, {time_start}-{time_end} (пара {number})\n"
                                f"*Аудитория:* {audience}\n"
                                f"*Преподаватель:* {teacher}\n\n"
                                f"До начала занятия осталось примерно {time_text}"
                            )

                            try:
                                await context.bot.send_message(
                                    chat_id=telegram_id,
                                    text=message,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Отправлено уведомление пользователю {telegram_id} о занятии {subject}")
                            except Exception as e:
                                logger.error(f"Ошибка при отправке уведомления пользователю {telegram_id}: {e}")
                except ValueError:
                    logger.error(f"Error parsing date/time: {date_str} {time_start}")

    except Exception as e:
        logger.error(f"Ошибка при проверке ближайших занятий: {e}")

def main() -> None:
    """Запускает бота."""
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()

    # Добавляем обработчики
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(handle_callback),
            ],
            SELECTING_GROUP: [
                CallbackQueryHandler(handle_callback),
            ],
            ENTERING_GROUP_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_name_input),
                CallbackQueryHandler(handle_callback),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_chat=True,
        name="main_conversation",
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", quick_search))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("tomorrow", tomorrow_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("subscriptions", subscriptions_command))

    # Добавляем задачи только если доступен job_queue
    job_queue = application.job_queue
    if job_queue is not None:
        # Обновление списка групп
        job_queue.run_once(update_groups_list, 5)  # Обновляем сразу после запуска
        job_queue.run_repeating(update_groups_list, interval=604800, first=86400)  # Каждую неделю

        # Обновление расписания
        job_queue.run_repeating(update_timetable_for_all_groups, interval=86400, first=10)  # Ежедневно

        # Проверка напоминаний
        job_queue.run_repeating(check_upcoming_lessons, interval=300, first=5)  # Каждые 5 минут
    else:
        logger.warning("JobQueue не доступна. Функции автоматического обновления и уведомлений отключены.")
        logger.warning("Установите python-telegram-bot[job-queue] для активации этих функций.")

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
