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
# BEGIN CHANGES: Added new states for enhanced functionality
SELECTING_GROUP, SELECTING_ACTION, ENTERING_GROUP_NAME, SELECTING_NOTIFICATION_TIME, SELECTING_DAILY_NOTIFICATION_TIME, \
    ENTERING_SUBJECT_NAME, ENTERING_TEACHER_NAME, SELECTING_UPDATE_PERIOD, ENTERING_ROOM_NUMBER = range(9)
# END CHANGES

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

# BEGIN CHANGES: Updated timetable update function to support custom date ranges
async def update_timetable_for_group(group_id, days=30):
    """Обновляет расписание для группы с указанным периодом"""
    try:
        # Получаем информацию о группе
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        # Отмечаем начало обновления
        db.start_update('timetable', group_id)

        logger.info(f"Обновление расписания для группы {group_name} (ID: {group_id}) на {days} дней")
        start_date = datetime.now().strftime('%d.%m.%Y')
        end_date = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')

        lessons = parse_timetable(group_id, start_date, end_date)

        if lessons:
            db.save_timetable(group_id, lessons)
            logger.info(f"Загружено {len(lessons)} занятий для группы {group_name}")

            # Запланировать следующее обновление через 24 часа
            next_update = datetime.now() + timedelta(hours=24)
            db.complete_update('timetable', group_id, next_update, 'completed')
            return True
        else:
            logger.warning(f"Не удалось получить расписание для группы {group_name}")

            # Если не удалось получить данные, пробуем через час
            next_update = datetime.now() + timedelta(hours=1)
            db.complete_update('timetable', group_id, next_update, 'failed')
            return False
    except Exception as e:
        logger.error(f"Ошибка при обновлении расписания для группы {group_id}: {e}")
        db.complete_update('timetable', group_id, datetime.now() + timedelta(hours=1), 'error')
        return False

async def update_timetable_for_all_groups():
    """Обновляет расписание для всех групп с учетом времени последнего обновления"""
    groups = db.get_all_groups()

    for _, group_name, group_id in groups:
        try:
            # Проверяем, нужно ли обновлять расписание для этой группы
            if not db.is_update_needed('timetable', group_id):
                logger.info(f"Пропуск обновления для группы {group_name} (ID: {group_id}) - еще не время")
                continue

            # Получаем настройки периода обновления для этой группы
            update_days = db.get_update_period_for_group(group_id)
            await update_timetable_for_group(group_id, update_days)

            # Добавляем задержку между запросами
            await asyncio.sleep(1)  # 1 секунда задержки
        except Exception as e:
            logger.error(f"Ошибка при обновлении расписания для группы {group_name}: {e}")
            db.complete_update('timetable', group_id, datetime.now() + timedelta(hours=1), 'error')

    logger.info("Обновление расписания завершено")
# END CHANGES

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
        # BEGIN CHANGES: Added new buttons for enhanced views
        [InlineKeyboardButton("Расписание на неделю", callback_data='week')],
        [InlineKeyboardButton("Расписание на месяц", callback_data='month')],
        [InlineKeyboardButton("Найти преподавателя", callback_data='find_teacher')],
        # END CHANGES
    ]

    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот для получения расписания занятий.\n\n"
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return SELECTING_ACTION

# BEGIN CHANGES: Enhanced callback handler with new functionality
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

    elif action in ['today', 'tomorrow', 'week', 'month', 'quarter']:
        # Получаем список подписок пользователя
        subscriptions = db.get_user_subscriptions(update.effective_user.id)

        if not subscriptions:
            await query.edit_message_text(
                "У вас нет активных подписок. Используйте команду '/start' чтобы подписаться на расписание группы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
            return SELECTING_ACTION

        # Настраиваем период отображения
        days_to_show = 1
        period_name = "Сегодня"

        if action == 'tomorrow':
            days_to_show = 1
            period_name = "Завтра"
            start_date = datetime.now() + timedelta(days=1)
        elif action == 'week':
            days_to_show = 7
            period_name = "На неделю"
            start_date = datetime.now()
        elif action == 'month':
            days_to_show = 30
            period_name = "На месяц"
            start_date = datetime.now()
        elif action == 'quarter':
            days_to_show = 90
            period_name = "На квартал"
            start_date = datetime.now()
        else:  # today
            days_to_show = 1
            period_name = "Сегодня"
            start_date = datetime.now()

        # Показываем для каждой группы отдельно если дней больше 1
        if days_to_show > 1:
            await query.edit_message_text(
                f"Расписание {period_name.lower()} для ваших групп:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )

            for _, group_name, group_external_id in subscriptions:
                await show_timetable_for_period(
                    context, update.effective_chat.id, group_external_id, group_name,
                    start_date, days_to_show, period_name
                )

            # Отправляем кнопку назад после всех расписаний
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Используйте кнопку для навигации:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )
        else:
            # Для одного дня показываем всё вместе
            date_str = start_date.strftime('%d.%m.%Y')

            message = f"Расписание на {period_name.lower()} ({date_str}):\n\n"
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
                message = f"На {period_name.lower()} ({date_str}) занятий не найдено."

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]]),
                parse_mode='Markdown'
            )

        return SELECTING_ACTION

    elif action == 'find_teacher':
        # Предлагаем способы поиска преподавателя
        keyboard = [
            [InlineKeyboardButton("Поиск по имени преподавателя", callback_data='search_teacher_name')],
            [InlineKeyboardButton("Поиск по аудитории", callback_data='search_teacher_room')],
            [InlineKeyboardButton("Назад", callback_data='back_to_main')],
        ]

        await query.edit_message_text(
            "Выберите способ поиска преподавателя:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action == 'search_teacher_name':
        await query.edit_message_text(
            "Введите имя преподавателя (или часть имени):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='find_teacher')]])
        )

        return ENTERING_TEACHER_NAME

    elif action == 'search_teacher_room':
        await query.edit_message_text(
            "Введите номер аудитории:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='find_teacher')]])
        )

        return ENTERING_ROOM_NUMBER

    elif action == 'back_to_main':
        # Возвращаемся в главное меню
        keyboard = [
            [InlineKeyboardButton("Подписаться на расписание", callback_data='subscribe')],
            [InlineKeyboardButton("Мои подписки", callback_data='my_subscriptions')],
            [InlineKeyboardButton("Ближайшие занятия", callback_data='upcoming_lessons')],
            [InlineKeyboardButton("Расписание на сегодня", callback_data='today')],
            [InlineKeyboardButton("Расписание на завтра", callback_data='tomorrow')],
            [InlineKeyboardButton("Расписание на неделю", callback_data='week')],
            [InlineKeyboardButton("Расписание на месяц", callback_data='month')],
            [InlineKeyboardButton("Найти преподавателя", callback_data='find_teacher')],
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
            # Обновляем расписание для выбранной группы на 30 дней
            await update_timetable_for_group(group_id)

            # Получаем название группы
            group_info = db.get_group_by_id(group_id)
            group_name = group_info[1] if group_info else "Unknown"

            # Предлагаем настроить ежедневные уведомления
            keyboard = [
                [InlineKeyboardButton("Настроить уведомления",
                                      callback_data=f"setup_daily_notifications_{group_id}")],
                [InlineKeyboardButton("Настроить позже", callback_data="back_to_main")]
            ]

            await query.edit_message_text(
                f"Вы успешно подписались на расписание группы {group_name}!\n\n"
                f"Хотите настроить ежедневные уведомления о занятиях?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                "Вы уже подписаны на эту группу или произошла ошибка.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
            )

        return SELECTING_ACTION

    elif action.startswith('setup_daily_notifications_'):
        # Настройка ежедневных уведомлений для группы
        group_id = action.split('_')[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("За 30 минут до начала первой пары",
                                  callback_data=f"daily_notify_30_{group_id}")],
            [InlineKeyboardButton("За 1 час до начала первой пары",
                                  callback_data=f"daily_notify_60_{group_id}")],
            [InlineKeyboardButton("За 1,5 часа до начала первой пары",
                                  callback_data=f"daily_notify_90_{group_id}")],
            [InlineKeyboardButton("За 2 часа до начала первой пары",
                                  callback_data=f"daily_notify_120_{group_id}")],
            [InlineKeyboardButton("Отключить ежедневные уведомления",
                                  callback_data=f"daily_notify_off_{group_id}")],
            [InlineKeyboardButton("Назад",
                                  callback_data=f"view_subscription_{group_id}")],
        ]

        await query.edit_message_text(
            f"Настройка ежедневных уведомлений для группы {group_name}\n\n"
            f"Выберите, за сколько времени до начала первой пары вы хотите получать уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('daily_notify_'):
        # Сохранение настроек ежедневных уведомлений
        parts = action.split('_')
        setting = parts[2]
        group_id = parts[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if setting == 'off':
            # Отключаем ежедневные уведомления
            db.toggle_daily_notifications(update.effective_user.id, group_id, False)
            message = f"Ежедневные уведомления для группы {group_name} отключены."
        else:
            # Устанавливаем время уведомления
            minutes = int(setting)
            db.update_daily_notification_settings(update.effective_user.id, group_id, minutes)
            db.toggle_daily_notifications(update.effective_user.id, group_id, True)

            time_text = ""
            if minutes < 60:
                time_text = f"за {minutes} минут"
            else:
                hours = minutes // 60
                mins = minutes % 60
                time_text = f"за {hours} час"
                if hours > 1 and hours < 5:
                    time_text += "а"
                elif hours >= 5:
                    time_text += "ов"
                if mins > 0:
                    time_text += f" {mins} минут"

            message = f"Настройки уведомлений обновлены. Вы будете получать ежедневные уведомления {time_text} до начала первой пары."

        keyboard = [
            [InlineKeyboardButton("Настроить уведомления о пропусках занятий",
                                  callback_data=f"setup_gap_notifications_{group_id}")],
            [InlineKeyboardButton("Назад к настройкам",
                                  callback_data=f"notification_settings_{group_id}")],
        ]

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('setup_gap_notifications_'):
        # Настройка уведомлений о парах после "окон"
        group_id = action.split('_')[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("За 15 минут до начала пары",
                                  callback_data=f"gap_notify_15_{group_id}")],
            [InlineKeyboardButton("За 30 минут до начала пары",
                                  callback_data=f"gap_notify_30_{group_id}")],
            [InlineKeyboardButton("За 1 час до начала пары",
                                  callback_data=f"gap_notify_60_{group_id}")],
            [InlineKeyboardButton("Отключить эти уведомления",
                                  callback_data=f"gap_notify_off_{group_id}")],
            [InlineKeyboardButton("Назад",
                                  callback_data=f"view_subscription_{group_id}")],
        ]

        await query.edit_message_text(
            f"Настройка уведомлений о парах после \"окон\" для группы {group_name}\n\n"
            f"Выберите, за сколько времени до начала пары после перерыва вы хотите получать уведомление:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('gap_notify_'):
        # Сохранение настроек уведомлений о парах после "окон"
        parts = action.split('_')
        setting = parts[2]
        group_id = parts[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if setting == 'off':
            # Отключаем уведомления о парах после окон
            db.toggle_gap_notifications(update.effective_user.id, group_id, False)
            message = f"Уведомления о парах после \"окон\" для группы {group_name} отключены."
        else:
            # Устанавливаем время уведомления
            minutes = int(setting)
            db.update_gap_notification_settings(update.effective_user.id, group_id, minutes)
            db.toggle_gap_notifications(update.effective_user.id, group_id, True)

            time_text = ""
            if minutes < 60:
                time_text = f"за {minutes} минут"
            else:
                hours = minutes // 60
                mins = minutes % 60
                time_text = f"за {hours} час"
                if hours > 1 and hours < 5:
                    time_text += "а"
                elif hours >= 5:
                    time_text += "ов"
                if mins > 0:
                    time_text += f" {mins} минут"

            message = f"Настройки уведомлений обновлены. Вы будете получать уведомления о парах после перерывов {time_text} до их начала."

        keyboard = [
            [InlineKeyboardButton("Настроить уведомления по предметам",
                                  callback_data=f"setup_subject_notifications_{group_id}")],
            [InlineKeyboardButton("Назад к настройкам",
                                  callback_data=f"notification_settings_{group_id}")],
        ]

        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('setup_subject_notifications_'):
        # Настройка уведомлений о конкретных предметах
        group_id = action.split('_')[3]

        # Просим ввести название предмета
        await query.edit_message_text(
            "Введите название предмета, о котором хотите получать уведомления "
            "(или часть названия, например, 'матем' для 'Математика'):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Назад", callback_data=f"notification_settings_{group_id}")
            ]])
        )

        # Сохраняем ID группы в контексте
        context.user_data['current_group_id'] = group_id

        return ENTERING_SUBJECT_NAME

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
            [InlineKeyboardButton("На месяц", callback_data=f"view_month_{group_id}")],
            [InlineKeyboardButton("Настройки уведомлений", callback_data=f"notification_settings_{group_id}")],
            [InlineKeyboardButton("Обновить расписание", callback_data=f"update_timetable_{group_id}")],
            [InlineKeyboardButton("Настроить период обновления", callback_data=f"update_period_{group_id}")],
            [InlineKeyboardButton("Назад к подпискам", callback_data='my_subscriptions')],
            [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
        ]

        await query.edit_message_text(
            f"Расписание группы {group_name}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('view_today_') or action.startswith('view_tomorrow_') or \
            action.startswith('view_week_') or action.startswith('view_month_') or \
            action.startswith('view_quarter_'):
        # Обработка просмотра расписания на разные периоды
        parts = action.split('_')
        view_type = parts[1]
        group_id = parts[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if view_type == 'today':
            # Показываем расписание на сегодня
            date = datetime.now()
            await show_timetable_for_date(
                update, context, group_id, group_name, date, "сегодня"
            )
        elif view_type == 'tomorrow':
            # Показываем расписание на завтра
            date = datetime.now() + timedelta(days=1)
            await show_timetable_for_date(
                update, context, group_id, group_name, date, "завтра"
            )
        else:
            # Показываем расписание на период (неделя/месяц/квартал)
            days = 7 if view_type == 'week' else 30 if view_type == 'month' else 90
            period_name = "неделю" if view_type == 'week' else "месяц" if view_type == 'month' else "квартал"

            await show_timetable_for_period(
                context, update.effective_chat.id, group_id, group_name,
                datetime.now(), days, period_name
            )

            # Отправляем кнопку назад
            keyboard = [
                [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
                [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
            ]

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Используйте кнопки для навигации:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        return SELECTING_ACTION

    elif action.startswith('update_timetable_'):
        # Принудительное обновление расписания
        group_id = action.split('_')[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        # Получаем текущие настройки периода обновления
        update_days = db.get_update_period_for_group(group_id)

        await query.edit_message_text(
            f"Обновляем расписание для группы {group_name} на {update_days} дней...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Отмена", callback_data=f"view_subscription_{group_id}")
            ]])
        )

        # Выполняем обновление
        success = await update_timetable_for_group(group_id, update_days)

        if success:
            await query.edit_message_text(
                f"Расписание для группы {group_name} успешно обновлено.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Вернуться к группе", callback_data=f"view_subscription_{group_id}")],
                    [InlineKeyboardButton("Главное меню", callback_data='back_to_main')]
                ])
            )
        else:
            await query.edit_message_text(
                f"Не удалось обновить расписание для группы {group_name}. Пожалуйста, попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Вернуться к группе", callback_data=f"view_subscription_{group_id}")],
                    [InlineKeyboardButton("Главное меню", callback_data='back_to_main')]
                ])
            )

        return SELECTING_ACTION

    elif action.startswith('update_period_'):
        # Настройка периода автоматического обновления
        group_id = action.split('_')[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("2 недели (14 дней)", callback_data=f"set_period_14_{group_id}")],
            [InlineKeyboardButton("1 месяц (30 дней)", callback_data=f"set_period_30_{group_id}")],
            [InlineKeyboardButton("3 месяца (90 дней)", callback_data=f"set_period_90_{group_id}")],
            [InlineKeyboardButton("Назад", callback_data=f"view_subscription_{group_id}")],
        ]

        await query.edit_message_text(
            f"Выберите период автоматического обновления расписания для группы {group_name}.\n\n"
            f"На этот период будут загружены данные при обновлении расписания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('set_period_'):
        # Установка периода обновления
        parts = action.split('_')
        days = int(parts[2])
        group_id = parts[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        # Сохраняем настройку периода
        db.set_update_period_for_group(group_id, days)

        # Предлагаем обновить расписание с новым периодом
        keyboard = [
            [InlineKeyboardButton("Обновить расписание сейчас", callback_data=f"update_timetable_{group_id}")],
            [InlineKeyboardButton("Вернуться без обновления", callback_data=f"view_subscription_{group_id}")],
        ]

        period_text = ""
        if days == 14:
            period_text = "2 недели"
        elif days == 30:
            period_text = "1 месяц"
        elif days == 90:
            period_text = "3 месяца"

        await query.edit_message_text(
            f"Период обновления расписания для группы {group_name} установлен на {period_text} ({days} дней).\n\n"
            f"Хотите обновить расписание с новыми настройками?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('notification_settings_'):
        # Настройки уведомлений для группы
        group_id = action.split('_')[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("Ежедневные уведомления", callback_data=f"setup_daily_notifications_{group_id}")],
            [InlineKeyboardButton("Уведомления о парах после перерывов", callback_data=f"setup_gap_notifications_{group_id}")],
            [InlineKeyboardButton("Уведомления по конкретным предметам", callback_data=f"setup_subject_notifications_{group_id}")],
            [InlineKeyboardButton("Уведомления о каждой паре", callback_data=f"setup_lesson_notifications_{group_id}")],
            [InlineKeyboardButton("Настройки по преподавателям", callback_data=f"setup_teacher_notifications_{group_id}")],
            [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
        ]

        await query.edit_message_text(
            f"Настройка уведомлений для группы {group_name}.\n\n"
            "Выберите тип уведомлений для настройки:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('setup_lesson_notifications_'):
        # Настройки уведомлений для каждой пары
        group_id = action.split('_')[3]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        keyboard = [
            [InlineKeyboardButton("За 15 минут", callback_data=f"notify_15_{group_id}")],
            [InlineKeyboardButton("За 30 минут", callback_data=f"notify_30_{group_id}")],
            [InlineKeyboardButton("За 1 час", callback_data=f"notify_60_{group_id}")],
            [InlineKeyboardButton("За 2 часа", callback_data=f"notify_120_{group_id}")],
            [InlineKeyboardButton("Выключить уведомления", callback_data=f"notify_off_{group_id}")],
            [InlineKeyboardButton("Назад к настройкам", callback_data=f"notification_settings_{group_id}")],
        ]

        await query.edit_message_text(
            f"Настройка уведомлений о каждой паре для группы {group_name}.\n\n"
            "Выберите, за сколько времени до начала занятий получать уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return SELECTING_ACTION

    elif action.startswith('setup_teacher_notifications_'):
        # Настройка уведомлений по конкретным преподавателям
        group_id = action.split('_')[3]

        # Просим ввести имя преподавателя
        await query.edit_message_text(
            "Введите имя преподавателя, о занятиях которого хотите получать уведомления "
            "(или часть имени, например, 'Иванов' или 'Петр'):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Назад", callback_data=f"notification_settings_{group_id}")
            ]])
        )

        # Сохраняем ID группы в контексте
        context.user_data['current_group_id'] = group_id

        return ENTERING_TEACHER_NAME

    elif action.startswith('notify_'):
        # Обработка настройки времени уведомления для каждой пары
        parts = action.split('_')
        setting = parts[1]
        group_id = parts[2]

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        if setting == 'off':
            # Отключаем уведомления
            db.toggle_notifications(update.effective_user.id, group_id, False)
            message = f"Уведомления о каждой паре для группы {group_name} отключены."
        else:
            # Устанавливаем время уведомления
            minutes = int(setting)
            db.update_notification_settings(update.effective_user.id, group_id, minutes)
            db.toggle_notifications(update.effective_user.id, group_id, True)

            time_text = ""
            if minutes < 60:
                time_text = f"за {minutes} минут"
            else:
                hours = minutes // 60
                mins = minutes % 60
                time_text = f"за {hours} час"
                if hours > 1:
                    time_text += "а" if hours < 5 else "ов"
                if mins > 0:
                    time_text += f" {mins} минут"

            message = f"Настройки уведомлений обновлены. Вы будете получать уведомления о каждой паре {time_text} до начала занятия."

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
# END CHANGES

# BEGIN CHANGES: Added helper functions for displaying timetables
async def show_timetable_for_date(update, context, group_id, group_name, date, period_name):
    """Показывает расписание на конкретную дату"""
    query = update.callback_query
    date_str = date.strftime('%d.%m.%Y')

    lessons = db.get_timetable_for_group(group_id, date_str)

    if not lessons:
        message = f"На {period_name} ({date_str}) для группы {group_name} занятий не найдено."
    else:
        message = f"Расписание на {period_name} ({date_str}) для группы {group_name}:\n\n"

        for lesson in lessons:
            date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

            message += (
                f"📚 {subject} ({lesson_type})\n"
                f"⏰ {time_start}-{time_end} (пара {number})\n"
                f"🏢 Аудитория: {audience}\n"
                f"👨‍🏫 Преподаватель: {teacher}\n\n"
            )

    keyboard = [
        [InlineKeyboardButton("Назад к расписанию", callback_data=f"view_subscription_{group_id}")],
        [InlineKeyboardButton("Главное меню", callback_data='back_to_main')],
    ]

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_timetable_for_period(context, chat_id, group_id, group_name, start_date, days, period_name):
    """Показывает расписание на указанный период"""
    message = f"Расписание на {period_name.lower()} для группы *{group_name}*:\n\n"
    has_lessons = False

    for i in range(days):
        date = start_date + timedelta(days=i)
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
        # Не показываем пустые дни, если период большой
        elif days <= 14:
            message += f"*{ru_day_name} ({date_str})*: занятий нет\n\n"

    if not has_lessons:
        message = f"На {period_name.lower()} для группы *{group_name}* занятий не найдено."

    # Разбиваем сообщение, если оно слишком длинное
    if len(message) > 4096:
        parts = [message[i:i+4096] for i in range(0, len(message), 4096)]

        for part in parts:
            await context.bot.send_message(
                chat_id=chat_id,
                text=part,
                parse_mode='Markdown'
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='Markdown'
        )
# END CHANGES

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

# BEGIN CHANGES: Added handlers for subject and teacher name inputs
async def handle_subject_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод названия предмета для уведомлений"""
    user_input = update.message.text.strip()

    # Получаем сохраненный ID группы из контекста
    group_id = context.user_data.get('current_group_id')
    if not group_id:
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, начните настройку заново.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Главное меню", callback_data='back_to_main')]])
        )
        return SELECTING_ACTION

    # Получаем название группы
    group_info = db.get_group_by_id(group_id)
    group_name = group_info[1] if group_info else "Unknown"

    # Добавляем предмет для уведомлений
    result = db.add_subject_notification(update.effective_user.id, group_id, user_input)

    if result:
        # Предлагаем настроить время уведомления
        keyboard = [
            [InlineKeyboardButton("За 15 минут", callback_data=f"subject_notify_15_{group_id}")],
            [InlineKeyboardButton("За 30 минут", callback_data=f"subject_notify_30_{group_id}")],
            [InlineKeyboardButton("За 1 час", callback_data=f"subject_notify_60_{group_id}")],
            [InlineKeyboardButton("За 2 часа", callback_data=f"subject_notify_120_{group_id}")],
        ]

        await update.message.reply_text(
            f"Добавлено уведомление для предмета, содержащего '{user_input}' для группы {group_name}.\n\n"
            f"Выберите, за сколько времени до начала занятия получать уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            f"Произошла ошибка при добавлении уведомления для предмета '{user_input}'.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Назад к настройкам", callback_data=f"notification_settings_{group_id}")
            ]])
        )

    return SELECTING_ACTION

async def handle_teacher_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод имени преподавателя для уведомлений или поиска"""
    user_input = update.message.text.strip()

    # Определяем операцию: настройка уведомлений или поиск расписания
    operation = context.user_data.get('teacher_operation', 'search')

    if operation == 'notifications':
        # Получаем сохраненный ID группы из контекста
        group_id = context.user_data.get('current_group_id')
        if not group_id:
            await update.message.reply_text(
                "Произошла ошибка. Пожалуйста, начните настройку заново.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Главное меню", callback_data='back_to_main')]])
            )
            return SELECTING_ACTION

        # Получаем название группы
        group_info = db.get_group_by_id(group_id)
        group_name = group_info[1] if group_info else "Unknown"

        # Добавляем преподавателя для уведомлений
        result = db.add_teacher_notification(update.effective_user.id, group_id, user_input)

        if result:
            # Предлагаем настроить время уведомления
            keyboard = [
                [InlineKeyboardButton("За 15 минут", callback_data=f"teacher_notify_15_{group_id}")],
                [InlineKeyboardButton("За 30 минут", callback_data=f"teacher_notify_30_{group_id}")],
                [InlineKeyboardButton("За 1 час", callback_data=f"teacher_notify_60_{group_id}")],
                [InlineKeyboardButton("За 2 часа", callback_data=f"teacher_notify_120_{group_id}")],
            ]

            await update.message.reply_text(
                f"Добавлено уведомление для преподавателя, имя которого содержит '{user_input}' для группы {group_name}.\n\n"
                f"Выберите, за сколько времени до начала занятия получать уведомления:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                f"Произошла ошибка при добавлении уведомления для преподавателя '{user_input}'.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Назад к настройкам", callback_data=f"notification_settings_{group_id}")
                ]])
            )

        return SELECTING_ACTION
    else:
        # Поиск расписания преподавателя
        # Минимальная длина запроса
        if len(user_input) < 3:
            await update.message.reply_text(
                "Пожалуйста, введите не менее 3 символов для поиска преподавателя.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="find_teacher")]])
            )
            return ENTERING_TEACHER_NAME

        # Ищем занятия этого преподавателя на ближайшие 5 дней
        teacher_lessons = db.find_teacher_lessons(user_input)

        if not teacher_lessons:
            await update.message.reply_text(
                f"Занятия преподавателя, имя которого содержит '{user_input}', не найдены на ближайшие 5 дней.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="find_teacher")]])
            )
            return SELECTING_ACTION

        # Формируем сообщение с расписанием преподавателя
        message = f"Занятия преподавателя, имя которого содержит '{user_input}', на ближайшие 5 дней:\n\n"

        # Группируем по преподавателям (если найдено несколько)
        grouped_lessons = {}

        for lesson in teacher_lessons:
            date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

            if teacher not in grouped_lessons:
                grouped_lessons[teacher] = []

            grouped_lessons[teacher].append({
                'date': date,
                'number': number,
                'time_start': time_start,
                'time_end': time_end,
                'subject': subject,
                'lesson_type': lesson_type,
                'audience': audience,
                'group_name': group_name
            })

        # Формируем сообщение по каждому найденному преподавателю
        for teacher, lessons in grouped_lessons.items():
            message += f"*Преподаватель: {teacher}*\n\n"

            # Группируем по датам
            lessons_by_date = {}
            for lesson in lessons:
                if lesson['date'] not in lessons_by_date:
                    lessons_by_date[lesson['date']] = []
                lessons_by_date[lesson['date']].append(lesson)

            # Сортируем даты
            for date in sorted(lessons_by_date.keys()):
                message += f"📅 *{date}*:\n"

                # Сортируем занятия по времени начала
                day_lessons = sorted(lessons_by_date[date], key=lambda x: x['time_start'])

                for lesson in day_lessons:
                    message += (
                        f"⏰ {lesson['time_start']}-{lesson['time_end']} (пара {lesson['number']})\n"
                        f"📚 {lesson['subject']} ({lesson['lesson_type']})\n"
                        f"👥 Группа: {lesson['group_name']}\n"
                        f"🏢 Аудитория: {lesson['audience']}\n\n"
                    )

        # Разбиваем сообщение, если оно слишком длинное
        if len(message) > 4096:
            parts = [message[i:i+4096] for i in range(0, len(message), 4096)]

            for i, part in enumerate(parts):
                if i == 0:
                    await update.message.reply_text(
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
                [InlineKeyboardButton("Искать другого преподавателя", callback_data="search_teacher_name")],
                [InlineKeyboardButton("Назад в меню", callback_data="back_to_main")],
            ]

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Используйте кнопки для навигации:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("Искать другого преподавателя", callback_data="search_teacher_name")],
                [InlineKeyboardButton("Назад в меню", callback_data="back_to_main")],
            ]

            await update.message.reply_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        return SELECTING_ACTION

async def handle_room_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод номера аудитории для поиска расписания"""
    user_input = update.message.text.strip()

    # Валидация ввода
    if len(user_input) < 1:
        await update.message.reply_text(
            "Пожалуйста, введите номер аудитории.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="find_teacher")]])
        )
        return ENTERING_ROOM_NUMBER

    # Ищем занятия в этой аудитории на ближайшие 5 дней
    room_lessons = db.find_room_lessons(user_input)

    if not room_lessons:
        await update.message.reply_text(
            f"Занятия в аудитории, содержащей '{user_input}', не найдены на ближайшие 5 дней.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="find_teacher")]])
        )
        return SELECTING_ACTION

    # Формируем сообщение с расписанием занятий в аудитории
    message = f"Занятия в аудитории, содержащей '{user_input}', на ближайшие 5 дней:\n\n"

    # Группируем по аудиториям (если найдено несколько)
    grouped_lessons = {}

    for lesson in room_lessons:
        date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

        if audience not in grouped_lessons:
            grouped_lessons[audience] = []

        grouped_lessons[audience].append({
            'date': date,
            'number': number,
            'time_start': time_start,
            'time_end': time_end,
            'subject': subject,
            'lesson_type': lesson_type,
            'teacher': teacher,
            'group_name': group_name
        })

    # Формируем сообщение по каждой найденной аудитории
    for audience, lessons in grouped_lessons.items():
        message += f"*Аудитория: {audience}*\n\n"

        # Фильтруем занятия ЭИОС (дистанционные)
        filtered_lessons = [l for l in lessons if "ЭИОС" not in l['audience']]

        if not filtered_lessons:
            message += "Найдены только дистанционные занятия (ЭИОС)\n\n"
            continue

        # Группируем по датам
        lessons_by_date = {}
        for lesson in filtered_lessons:
            if lesson['date'] not in lessons_by_date:
                lessons_by_date[lesson['date']] = []
            lessons_by_date[lesson['date']].append(lesson)

        # Сортируем даты
        for date in sorted(lessons_by_date.keys()):
            message += f"📅 *{date}*:\n"

            # Сортируем занятия по времени начала
            day_lessons = sorted(lessons_by_date[date], key=lambda x: x['time_start'])

            for lesson in day_lessons:
                message += (
                    f"⏰ {lesson['time_start']}-{lesson['time_end']} (пара {lesson['number']})\n"
                    f"📚 {lesson['subject']} ({lesson['lesson_type']})\n"
                    f"👥 Группа: {lesson['group_name']}\n"
                    f"👨‍🏫 Преподаватель: {lesson['teacher']}\n\n"
                )

    # Разбиваем сообщение, если оно слишком длинное
    if len(message) > 4096:
        parts = [message[i:i+4096] for i in range(0, len(message), 4096)]

        for i, part in enumerate(parts):
            if i == 0:
                await update.message.reply_text(
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
            [InlineKeyboardButton("Искать другую аудиторию", callback_data="search_teacher_room")],
            [InlineKeyboardButton("Назад в меню", callback_data="back_to_main")],
        ]

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Используйте кнопки для навигации:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        keyboard = [
            [InlineKeyboardButton("Искать другую аудиторию", callback_data="search_teacher_room")],
            [InlineKeyboardButton("Назад в меню", callback_data="back_to_main")],
        ]

        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    return SELECTING_ACTION
# END CHANGES

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с помощью, когда пользователь вводит /help"""
    help_text = (
        "Список доступных команд:\n\n"
        "/start - Запустить бота и показать главное меню\n"
        "/search [название] - Быстрый поиск группы по названию\n"
        "/today - Расписание на сегодня для ваших подписок\n"
        "/tomorrow - Расписание на завтра для ваших подписок\n"
        "/week - Расписание на неделю для ваших подписок\n"
        # BEGIN CHANGES: Added new commands
        "/month - Расписание на месяц для ваших подписок\n"
        "/quarter - Расписание на квартал для ваших подписок\n"
        "/teacher [фамилия] - Поиск расписания преподавателя\n"
        "/room [номер] - Поиск расписания по аудитории\n"
        # END CHANGES
        "/subscriptions - Управление подписками\n"
        "/help - Показать эту справку\n\n"
        "С помощью этого бота вы можете:\n"
        "- Подписаться на расписание выбранных групп\n"
        "- Получать уведомления о предстоящих занятиях\n"
        "- Просматривать расписание на сегодня, завтра, неделю или месяц\n"
        "- Настроить различные типы уведомлений\n"
        "- Искать расписание преподавателей и аудиторий"
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

# BEGIN CHANGES: Added new commands for extended timeframes
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на сегодня"""
    await show_timetable_command(update, context, days=1, offset=0, period_name="сегодня")

async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на завтра"""
    await show_timetable_command(update, context, days=1, offset=1, period_name="завтра")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на неделю"""
    await show_timetable_command(update, context, days=7, offset=0, period_name="неделю")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на месяц"""
    await show_timetable_command(update, context, days=30, offset=0, period_name="месяц")

async def quarter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание на квартал"""
    await show_timetable_command(update, context, days=90, offset=0, period_name="квартал")

async def show_timetable_command(update: Update, context: ContextTypes.DEFAULT_TYPE, days=1, offset=0, period_name="сегодня") -> None:
    """Обобщенная функция для показа расписания на разные периоды"""
    user_id = update.effective_user.id

    # Получаем список подписок пользователя
    subscriptions = db.get_user_subscriptions(user_id)

    if not subscriptions:
        await update.message.reply_text(
            "У вас нет активных подписок. Используйте команду /start чтобы подписаться на расписание группы."
        )
        return

    # Если период большой, отправляем отдельные сообщения для каждой группы
    if days > 1:
        await update.message.reply_text(
            f"Расписание на {period_name} для ваших групп:"
        )

        for _, group_name, group_external_id in subscriptions:
            await show_timetable_for_period(
                context, update.effective_chat.id, group_external_id, group_name,
                datetime.now() + timedelta(days=offset), days, period_name
            )
    else:
        # Для одного дня показываем всё в одном сообщении
        date = datetime.now() + timedelta(days=offset)
        date_str = date.strftime('%d.%m.%Y')

        message = f"Расписание на {period_name} ({date_str}):\n\n"
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
            message = f"На {period_name} ({date_str}) занятий не найдено."

        await update.message.reply_text(
            message,
            parse_mode='Markdown'
        )

async def teacher_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Поиск расписания преподавателя по аргументу команды"""
    if not context.args:
        await update.message.reply_text(
            "Пожалуйста, укажите имя преподавателя или его часть.\n"
            "Пример: /teacher Иванов"
        )
        return ConversationHandler.END

    search_query = ' '.join(context.args).strip()

    # Минимальная длина запроса
    if len(search_query) < 3:
        await update.message.reply_text(
            "Введите не менее 3 символов для поиска преподавателя."
        )
        return ConversationHandler.END

    # Ищем занятия этого преподавателя на ближайшие 5 дней
    teacher_lessons = db.find_teacher_lessons(search_query)

    if not teacher_lessons:
        await update.message.reply_text(
            f"Занятия преподавателя, имя которого содержит '{search_query}', не найдены на ближайшие 5 дней."
        )
        return ConversationHandler.END

    # Формируем сообщение с расписанием преподавателя
    message = f"Занятия преподавателя, имя которого содержит '{search_query}', на ближайшие 5 дней:\n\n"

    # Группируем по преподавателям (если найдено несколько)
    grouped_lessons = {}

    for lesson in teacher_lessons:
        date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

        if teacher not in grouped_lessons:
            grouped_lessons[teacher] = []

        grouped_lessons[teacher].append({
            'date': date,
            'number': number,
            'time_start': time_start,
            'time_end': time_end,
            'subject': subject,
            'lesson_type': lesson_type,
            'audience': audience,
            'group_name': group_name
        })

    # Формируем сообщение по каждому найденному преподавателю
    for teacher, lessons in grouped_lessons.items():
        message += f"*Преподаватель: {teacher}*\n\n"

        # Группируем по датам
        lessons_by_date = {}
        for lesson in lessons:
            if lesson['date'] not in lessons_by_date:
                lessons_by_date[lesson['date']] = []
            lessons_by_date[lesson['date']].append(lesson)

        # Сортируем даты
        for date in sorted(lessons_by_date.keys()):
            message += f"📅 *{date}*:\n"

            # Сортируем занятия по времени начала
            day_lessons = sorted(lessons_by_date[date], key=lambda x: x['time_start'])

            for lesson in day_lessons:
                message += (
                    f"⏰ {lesson['time_start']}-{lesson['time_end']} (пара {lesson['number']})\n"
                    f"📚 {lesson['subject']} ({lesson['lesson_type']})\n"
                    f"👥 Группа: {lesson['group_name']}\n"
                    f"🏢 Аудитория: {lesson['audience']}\n\n"
                )

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

    return ConversationHandler.END

async def room_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Поиск расписания по аудитории"""
    if not context.args:
        await update.message.reply_text(
            "Пожалуйста, укажите номер аудитории.\n"
            "Пример: /room 314"
        )
        return ConversationHandler.END

    search_query = ' '.join(context.args).strip()

    # Ищем занятия в этой аудитории на ближайшие 5 дней
    room_lessons = db.find_room_lessons(search_query)

    if not room_lessons:
        await update.message.reply_text(
            f"Занятия в аудитории, содержащей '{search_query}', не найдены на ближайшие 5 дней."
        )
        return ConversationHandler.END

    # Формируем сообщение с расписанием занятий в аудитории
    message = f"Занятия в аудитории, содержащей '{search_query}', на ближайшие 5 дней:\n\n"

    # Группируем по аудиториям (если найдено несколько)
    grouped_lessons = {}

    for lesson in room_lessons:
        date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

        # Фильтруем ЭИОС (дистанционные занятия)
        if "ЭИОС" in audience:
            continue

        if audience not in grouped_lessons:
            grouped_lessons[audience] = []

        grouped_lessons[audience].append({
            'date': date,
            'number': number,
            'time_start': time_start,
            'time_end': time_end,
            'subject': subject,
            'lesson_type': lesson_type,
            'teacher': teacher,
            'group_name': group_name
        })

    if not grouped_lessons:
        await update.message.reply_text(
            f"Найдены только дистанционные занятия (ЭИОС) для аудитории '{search_query}'."
        )
        return ConversationHandler.END

    # Формируем сообщение по каждой найденной аудитории
    for audience, lessons in grouped_lessons.items():
        message += f"*Аудитория: {audience}*\n\n"

        # Группируем по датам
        lessons_by_date = {}
        for lesson in lessons:
            if lesson['date'] not in lessons_by_date:
                lessons_by_date[lesson['date']] = []
            lessons_by_date[lesson['date']].append(lesson)

        # Сортируем даты
        for date in sorted(lessons_by_date.keys()):
            message += f"📅 *{date}*:\n"

            # Сортируем занятия по времени начала
            day_lessons = sorted(lessons_by_date[date], key=lambda x: x['time_start'])

            for lesson in day_lessons:
                message += (
                    f"⏰ {lesson['time_start']}-{lesson['time_end']} (пара {lesson['number']})\n"
                    f"📚 {lesson['subject']} ({lesson['lesson_type']})\n"
                    f"👥 Группа: {lesson['group_name']}\n"
                    f"👨‍🏫 Преподаватель: {lesson['teacher']}\n\n"
                )

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

    return ConversationHandler.END
# END CHANGES

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

# BEGIN CHANGES: Enhanced notification system with multiple types
async def check_upcoming_lessons(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет ближайшие занятия и отправляет уведомления"""
    try:
        current_time = datetime.now()
        current_date = current_time.strftime('%d.%m.%Y')

        # 1. Проверка ежедневных уведомлений
        daily_notifications = db.get_daily_notifications_to_send(current_date)
        for notification in daily_notifications:
            telegram_id, notify_before_minutes, first_lesson_time, group_name, group_id = notification

            try:
                # Парсим время первого занятия
                lesson_time = datetime.strptime(f"{current_date} {first_lesson_time}", '%d.%m.%Y %H:%M')

                # Время для отправки уведомления
                notify_time = lesson_time - timedelta(minutes=notify_before_minutes)

                # Если время для уведомления наступило (с точностью до 5 минут)
                time_diff_minutes = (notify_time - current_time).total_seconds() / 60

                if 0 <= time_diff_minutes <= 5:
                    # Получаем расписание на сегодня для формирования уведомления
                    lessons = db.get_timetable_for_group(group_id, current_date)

                    if lessons:
                        message = f"🔔 *Ежедневное напоминание о занятиях* 🔔\n\n"
                        message += f"*Группа:* {group_name}\n"
                        message += f"*Дата:* {current_date}\n\n"

                        for i, lesson in enumerate(lessons, 1):
                            date, number, time_start, time_end, subject, lesson_type, audience, teacher = lesson

                            message += (
                                f"*Пара {number}* ({time_start}-{time_end})\n"
                                f"📚 {subject} ({lesson_type})\n"
                                f"🏢 Аудитория: {audience}\n"
                                f"👨‍🏫 Преподаватель: {teacher}\n\n"
                            )

                        await context.bot.send_message(
                            chat_id=telegram_id,
                            text=message,
                            parse_mode='Markdown'
                        )
                        logger.info(f"Отправлено ежедневное уведомление пользователю {telegram_id} для группы {group_name}")

            except Exception as e:
                logger.error(f"Ошибка при отправке ежедневного уведомления: {e}")

        # 2. Проверка уведомлений о пропусках в расписании
        gap_notifications = db.get_gap_notifications_to_send()

        for notification in gap_notifications:
            telegram_id, notify_before_minutes, lesson_date, lesson_time, lesson_info = notification

            try:
                # Парсим время занятия после "окна"
                lesson_datetime = datetime.strptime(f"{lesson_date} {lesson_time}", '%d.%m.%Y %H:%M')

                # Время для отправки уведомления
                notify_time = lesson_datetime - timedelta(minutes=notify_before_minutes)

                # Если время для уведомления наступило (с точностью до 5 минут)
                time_diff_minutes = (notify_time - current_time).total_seconds() / 60

                if 0 <= time_diff_minutes <= 5:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson_info

                    message = (
                        f"⚠️ *Напоминание о паре после перерыва* ⚠️\n\n"
                        f"*Группа:* {group_name}\n"
                        f"*Предмет:* {subject} ({lesson_type})\n"
                        f"*Когда:* {date}, {time_start}-{time_end} (пара {number})\n"
                        f"*Аудитория:* {audience}\n"
                        f"*Преподаватель:* {teacher}\n\n"
                    )

                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Отправлено уведомление о паре после перерыва пользователю {telegram_id} о предмете {subject}")

            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления о паре после перерыва: {e}")

        # 3. Проверка уведомлений о конкретных предметах
        subject_notifications = db.get_subject_notifications_to_send()

        for notification in subject_notifications:
            telegram_id, notify_before_minutes, lesson_date, lesson_time, subject_pattern, lesson_info = notification

            try:
                # Парсим время занятия
                lesson_datetime = datetime.strptime(f"{lesson_date} {lesson_time}", '%d.%m.%Y %H:%M')

                # Время для отправки уведомления
                notify_time = lesson_datetime - timedelta(minutes=notify_before_minutes)

                # Если время для уведомления наступило (с точностью до 5 минут)
                time_diff_minutes = (notify_time - current_time).total_seconds() / 60

                if 0 <= time_diff_minutes <= 5:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson_info

                    message = (
                        f"📘 *Напоминание о предмете* 📘\n\n"
                        f"*Группа:* {group_name}\n"
                        f"*Предмет:* {subject} ({lesson_type})\n"
                        f"*Когда:* {date}, {time_start}-{time_end} (пара {number})\n"
                        f"*Аудитория:* {audience}\n"
                        f"*Преподаватель:* {teacher}\n\n"
                    )

                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Отправлено уведомление о предмете '{subject_pattern}' пользователю {telegram_id}")

            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления о предмете: {e}")

        # 4. Проверка уведомлений о занятиях конкретных преподавателей
        teacher_notifications = db.get_teacher_notifications_to_send()

        for notification in teacher_notifications:
            telegram_id, notify_before_minutes, lesson_date, lesson_time, teacher_pattern, lesson_info = notification

            try:
                # Парсим время занятия
                lesson_datetime = datetime.strptime(f"{lesson_date} {lesson_time}", '%d.%m.%Y %H:%M')

                # Время для отправки уведомления
                notify_time = lesson_datetime - timedelta(minutes=notify_before_minutes)

                # Если время для уведомления наступило (с точностью до 5 минут)
                time_diff_minutes = (notify_time - current_time).total_seconds() / 60

                if 0 <= time_diff_minutes <= 5:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson_info

                    message = (
                        f"👨‍🏫 *Напоминание о занятии преподавателя* 👨‍🏫\n\n"
                        f"*Группа:* {group_name}\n"
                        f"*Преподаватель:* {teacher}\n"
                        f"*Предмет:* {subject} ({lesson_type})\n"
                        f"*Когда:* {date}, {time_start}-{time_end} (пара {number})\n"
                        f"*Аудитория:* {audience}\n\n"
                    )

                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Отправлено уведомление о преподавателе '{teacher_pattern}' пользователю {telegram_id}")

            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления о преподавателе: {e}")

        # 5. Проверка стандартных уведомлений для всех занятий
        general_notifications = db.get_general_lesson_notifications_to_send()

        for notification in general_notifications:
            telegram_id, notify_before_minutes, lesson_date, lesson_time, lesson_info = notification

            try:
                # Парсим время занятия
                lesson_datetime = datetime.strptime(f"{lesson_date} {lesson_time}", '%d.%m.%Y %H:%M')

                # Время для отправки уведомления
                notify_time = lesson_datetime - timedelta(minutes=notify_before_minutes)

                # Если время для уведомления наступило (с точностью до 5 минут)
                time_diff_minutes = (notify_time - current_time).total_seconds() / 60

                if 0 <= time_diff_minutes <= 5:
                    date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson_info

                    # Форматируем время уведомления
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
                        f"*Когда:* {date}, {time_start}-{time_end} (пара {number})\n"
                        f"*Аудитория:* {audience}\n"
                        f"*Преподаватель:* {teacher}\n\n"
                        f"До начала занятия осталось примерно {time_text}"
                    )

                    await context.bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Отправлено стандартное уведомление пользователю {telegram_id} о занятии {subject}")

            except Exception as e:
                logger.error(f"Ошибка при отправке стандартного уведомления: {e}")

    except Exception as e:
        logger.error(f"Ошибка при проверке ближайших занятий: {e}")
        import traceback
        logger.error(traceback.format_exc())
# END CHANGES

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
            # BEGIN CHANGES: Added new handlers for enhanced functionality
            ENTERING_SUBJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subject_name_input),
                CallbackQueryHandler(handle_callback),
            ],
            ENTERING_TEACHER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_teacher_name_input),
                CallbackQueryHandler(handle_callback),
            ],
            ENTERING_ROOM_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_room_number_input),
                CallbackQueryHandler(handle_callback),
            ],
            # END CHANGES
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
    # BEGIN CHANGES: Added new command handlers
    application.add_handler(CommandHandler("month", month_command))
    application.add_handler(CommandHandler("quarter", quarter_command))
    application.add_handler(CommandHandler("teacher", teacher_command))
    application.add_handler(CommandHandler("room", room_command))
    # END CHANGES
    application.add_handler(CommandHandler("subscriptions", subscriptions_command))

    # Добавляем задачи только если доступен job_queue
    job_queue = application.job_queue
    if job_queue is not None:
        # Обновление списка групп
        job_queue.run_once(update_groups_list, 5)  # Обновляем сразу после запуска
        job_queue.run_repeating(update_groups_list, interval=604800, first=86400)  # Каждую неделю

        # Обновление расписания
        job_queue.run_repeating(update_timetable_for_all_groups, interval=86400, first=10)  # Ежедневно

        # Проверка напоминаний - теперь чаще для большей точности
        job_queue.run_repeating(check_upcoming_lessons, interval=120, first=5)  # Каждые 2 минуты
    else:
        logger.warning("JobQueue не доступна. Функции автоматического обновления и уведомлений отключены.")
        logger.warning("Установите python-telegram-bot[job-queue] для активации этих функций.")

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
