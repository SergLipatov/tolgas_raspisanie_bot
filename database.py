import sqlite3
import re
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_name='timetable_bot.db'):
        try:
            self.conn = sqlite3.connect(db_name)
            self.cursor = self.conn.cursor()
            self.init_db()
            logger.info(f"Database initialized: {db_name}")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            raise

    def init_db(self):
        """Инициализация базы данных"""
        # Таблица с группами
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            group_id INTEGER NOT NULL UNIQUE
        )
        ''')

        # Таблица с расписанием
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            number INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            subject TEXT NOT NULL,
            lesson_type TEXT NOT NULL,
            audience TEXT,
            teacher TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups (group_id)
        )
        ''')

        # Таблица с пользователями
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Таблица подписок на группы
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            notifications_enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (group_id) REFERENCES groups (group_id),
            UNIQUE(user_id, group_id)
        )
        ''')

        # Таблица с настройками уведомлений для каждой подписки
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_settings (
            id INTEGER PRIMARY KEY,
            subscription_id INTEGER UNIQUE NOT NULL,
            notify_before_minutes INTEGER DEFAULT 30,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions (id)
        )
        ''')

        # Таблица для хранения информации об обновлениях
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_info (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            last_update TIMESTAMP,
            next_update TIMESTAMP,
            status TEXT,
            UNIQUE(entity_type, entity_id)
        )
        ''')

        self.conn.commit()

    def add_group(self, name, group_id):
        """Добавляет группу в базу данных"""
        try:
            self.cursor.execute(
                "INSERT OR IGNORE INTO groups (name, group_id) VALUES (?, ?)",
                (name, group_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding group: {e}")
            return False

    def get_all_groups(self):
        """Возвращает список всех групп"""
        try:
            self.cursor.execute("SELECT id, name, group_id FROM groups ORDER BY name")
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting groups: {e}")
            return []

    def search_groups_by_name(self, search_query):
        """Комплексный поиск групп с поддержкой аббревиатур и кириллицы"""
        try:
            # Нормализуем запрос
            original_query = search_query.strip()
            search_query = original_query.lower()

            # Специальные случаи для аббревиатур
            special_cases = {
                "боз": ["бози"],
                "бип": ["бипз", "бипо"],
                # Добавьте другие специальные случаи
            }

            additional_queries = []
            for special_pattern, related_patterns in special_cases.items():
                if search_query == special_pattern or search_query.startswith(special_pattern):
                    additional_queries.extend(related_patterns)

            # Получаем все группы
            self.cursor.execute("SELECT id, name, group_id FROM groups ORDER BY name")
            all_groups = self.cursor.fetchall()

            # Первый проход: точное соответствие
            exact_matches = []
            partial_matches = []

            for group in all_groups:
                group_name = group[1]
                group_name_lower = group_name.lower()

                # Точное соответствие
                if search_query == group_name_lower:
                    exact_matches.append(group)
                    continue

                # Частичное соответствие (основной запрос)
                if search_query in group_name_lower:
                    partial_matches.append(group)
                    continue

                # Проверка аббревиатуры (например, "БОЗ" должен находить "БОЗИоз23")
                if len(search_query) >= 2 and all(c.isalpha() for c in search_query):
                    # Извлекаем начальные заглавные буквы из названия группы
                    abbr_match = re.match(r'([А-ЯA-Z]+)', group_name)
                    if abbr_match:
                        abbr = abbr_match.group(1).lower()
                        if search_query in abbr:
                            partial_matches.append(group)
                            continue

                # Проверяем дополнительные запросы для специальных случаев
                if any(query in group_name_lower for query in additional_queries):
                    partial_matches.append(group)
                    continue

            # Возвращаем точные совпадения, а затем частичные
            return exact_matches + partial_matches
        except Exception as e:
            logger.error(f"Error searching groups by name: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def get_group_by_id(self, group_id):
        """Получает информацию о группе по её ID"""
        try:
            self.cursor.execute(
                "SELECT id, name, group_id FROM groups WHERE group_id = ?",
                (group_id,)
            )
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f"Error getting group by ID: {e}")
            return None

    def save_timetable(self, group_id, lessons):
        """Сохраняет расписание в базу данных"""
        try:
            # Удаляем старое расписание для этой группы
            self.cursor.execute("DELETE FROM lessons WHERE group_id = ?", (group_id,))

            # Добавляем новое расписание
            for lesson in lessons:
                self.cursor.execute(
                    """
                    INSERT INTO lessons 
                    (group_id, date, number, time_start, time_end, subject, lesson_type, audience, teacher) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        lesson['date'],
                        lesson['number'],
                        lesson['time_start'],
                        lesson['time_end'],
                        lesson['subject'],
                        lesson['type'],
                        lesson['audience'],
                        lesson['teacher']
                    )
                )

            self.conn.commit()
            logger.info(f"Saved {len(lessons)} lessons for group ID {group_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving timetable: {e}")
            self.conn.rollback()
            return False

    def add_user(self, telegram_id, username=None, first_name=None, last_name=None):
        """Добавляет или обновляет пользователя"""
        try:
            self.cursor.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, last_name) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(excluded.username, username),
                first_name = COALESCE(excluded.first_name, first_name),
                last_name = COALESCE(excluded.last_name, last_name)
                """,
                (telegram_id, username, first_name, last_name)
            )
            self.conn.commit()

            # Возвращаем ID пользователя
            self.cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            user_id = self.cursor.fetchone()[0]
            logger.info(f"User processed: {telegram_id} (ID: {user_id})")
            return user_id
        except Exception as e:
            logger.error(f"Error adding user {telegram_id}: {e}")
            return None

    def subscribe_to_group(self, user_id, group_id):
        """Подписывает пользователя на группу"""
        try:
            self.cursor.execute(
                "INSERT OR IGNORE INTO subscriptions (user_id, group_id) VALUES (?, ?)",
                (user_id, group_id)
            )
            rows_affected = self.cursor.rowcount
            self.conn.commit()

            if rows_affected == 0:
                logger.info(f"User {user_id} is already subscribed to group {group_id}")
                return False

            # Получаем ID подписки
            self.cursor.execute(
                "SELECT id FROM subscriptions WHERE user_id = ? AND group_id = ?",
                (user_id, group_id)
            )
            subscription_id = self.cursor.fetchone()[0]

            # Добавляем настройки уведомлений по умолчанию
            self.cursor.execute(
                "INSERT OR IGNORE INTO notification_settings (subscription_id) VALUES (?)",
                (subscription_id,)
            )
            self.conn.commit()

            logger.info(f"User {user_id} subscribed to group {group_id}")
            return True
        except Exception as e:
            logger.error(f"Error subscribing user {user_id} to group {group_id}: {e}")
            return False

    def unsubscribe_from_group(self, user_id, group_id):
        """Отписывает пользователя от группы"""
        try:
            # Получаем ID подписки
            self.cursor.execute(
                """
                SELECT s.id FROM subscriptions s
                JOIN users u ON s.user_id = u.id
                WHERE u.telegram_id = ? AND s.group_id = ?
                """,
                (user_id, group_id)
            )
            result = self.cursor.fetchone()

            if not result:
                logger.warning(f"Subscription not found: user {user_id}, group {group_id}")
                return False

            subscription_id = result[0]

            # Удаляем настройки уведомлений
            self.cursor.execute(
                "DELETE FROM notification_settings WHERE subscription_id = ?",
                (subscription_id,)
            )

            # Удаляем подписку
            self.cursor.execute(
                "DELETE FROM subscriptions WHERE id = ?",
                (subscription_id,)
            )

            self.conn.commit()
            logger.info(f"User {user_id} unsubscribed from group {group_id}")
            return True
        except Exception as e:
            logger.error(f"Error unsubscribing user {user_id} from group {group_id}: {e}")
            return False

    def get_user_subscriptions(self, telegram_id):
        """Получает список подписок пользователя"""
        try:
            self.cursor.execute(
                """
                SELECT g.id, g.name, g.group_id 
                FROM groups g
                JOIN subscriptions s ON g.group_id = s.group_id
                JOIN users u ON s.user_id = u.id
                WHERE u.telegram_id = ?
                ORDER BY g.name
                """,
                (telegram_id,)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting subscriptions for user {telegram_id}: {e}")
            return []

    def get_timetable_for_group(self, group_id, date=None):
        """Получает расписание для группы на указанную дату"""
        try:
            if date is None:
                date = datetime.now().strftime('%d.%m.%Y')

            self.cursor.execute(
                """
                SELECT date, number, time_start, time_end, subject, lesson_type, audience, teacher 
                FROM lessons 
                WHERE group_id = ? AND date = ?
                ORDER BY time_start
                """,
                (group_id, date)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting timetable for group {group_id} on date {date}: {e}")
            return []

    def get_timetable_for_period(self, group_id, start_date, end_date):
        """Получает расписание для группы на указанный период"""
        try:
            self.cursor.execute(
                """
                SELECT date, number, time_start, time_end, subject, lesson_type, audience, teacher 
                FROM lessons 
                WHERE group_id = ? AND date >= ? AND date <= ?
                ORDER BY date, time_start
                """,
                (group_id, start_date, end_date)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting timetable for period: {e}")
            return []

    def get_upcoming_lessons(self, group_id, hours=24):
        """Получает ближайшие занятия для группы в течение указанного количества часов"""
        try:
            now = datetime.now()
            today = now.strftime('%d.%m.%Y')
            tomorrow = (now + timedelta(days=1)).strftime('%d.%m.%Y')

            # Получаем занятия на сегодня и завтра
            self.cursor.execute(
                """
                SELECT date, number, time_start, time_end, subject, lesson_type, audience, teacher 
                FROM lessons 
                WHERE group_id = ? AND (date = ? OR date = ?)
                ORDER BY date, time_start
                """,
                (group_id, today, tomorrow)
            )

            all_lessons = self.cursor.fetchall()
            upcoming_lessons = []

            for lesson in all_lessons:
                lesson_date_str = lesson[0]
                lesson_time_str = lesson[2]

                try:
                    # Парсим дату и время занятия
                    lesson_datetime = datetime.strptime(
                        f"{lesson_date_str} {lesson_time_str}",
                        '%d.%m.%Y %H:%M'
                    )

                    # Проверяем, находится ли занятие в указанном временном интервале
                    time_diff = (lesson_datetime - now).total_seconds() / 3600  # разница в часах

                    if 0 <= time_diff <= hours:
                        upcoming_lessons.append(lesson)
                except ValueError:
                    logger.error(f"Error parsing date/time: {lesson_date_str} {lesson_time_str}")

            return upcoming_lessons
        except Exception as e:
            logger.error(f"Error getting upcoming lessons: {e}")
            return []

    def get_users_to_notify(self, group_id, lesson_datetime):
        """Получает список пользователей для уведомления о конкретном занятии"""
        try:
            self.cursor.execute(
                """
                SELECT u.telegram_id, ns.notify_before_minutes 
                FROM users u
                JOIN subscriptions s ON u.id = s.user_id
                JOIN notification_settings ns ON s.id = ns.subscription_id
                WHERE s.group_id = ? AND s.notifications_enabled = 1
                """,
                (group_id,)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting users to notify: {e}")
            return []

    def update_notification_settings(self, telegram_id, group_id, notify_before_minutes):
        """Обновляет настройки уведомлений для подписки"""
        try:
            self.cursor.execute(
                """
                UPDATE notification_settings
                SET notify_before_minutes = ?
                WHERE subscription_id IN (
                    SELECT s.id FROM subscriptions s
                    JOIN users u ON s.user_id = u.id
                    WHERE u.telegram_id = ? AND s.group_id = ?
                )
                """,
                (notify_before_minutes, telegram_id, group_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating notification settings: {e}")
            return False

    def toggle_notifications(self, telegram_id, group_id, enabled):
        """Включает или выключает уведомления для подписки"""
        try:
            self.cursor.execute(
                """
                UPDATE subscriptions
                SET notifications_enabled = ?
                WHERE id IN (
                    SELECT s.id FROM subscriptions s
                    JOIN users u ON s.user_id = u.id
                    WHERE u.telegram_id = ? AND s.group_id = ?
                )
                """,
                (1 if enabled else 0, telegram_id, group_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error toggling notifications: {e}")
            return False

    def save_update_info(self, entity_type, entity_id=None, next_update=None):
        """Сохраняет информацию о последнем обновлении и запланированном следующем обновлении

        :param entity_type: тип сущности ('timetable', 'groups', etc.)
        :param entity_id: ID сущности (например, ID группы или None для общих обновлений)
        :param next_update: datetime объект со временем следующего обновления
        """
        try:
            now = datetime.now()
            next_update_str = next_update.strftime('%Y-%m-%d %H:%M:%S') if next_update else None

            self.cursor.execute(
                """
                INSERT INTO update_info (entity_type, entity_id, last_update, next_update, status)
                VALUES (?, ?, ?, ?, 'completed')
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                last_update = excluded.last_update,
                next_update = excluded.next_update,
                status = excluded.status
                """,
                (entity_type, entity_id, now.strftime('%Y-%m-%d %H:%M:%S'), next_update_str)
            )
            self.conn.commit()
            logger.info(f"Update info saved for {entity_type} {entity_id}, next update: {next_update_str}")
            return True
        except Exception as e:
            logger.error(f"Error saving update info: {e}")
            return False

    def get_update_info(self, entity_type, entity_id=None):
        """Получает информацию о последнем обновлении

        :return: словарь с информацией или None, если нет данных
        """
        try:
            self.cursor.execute(
                """
                SELECT last_update, next_update, status
                FROM update_info
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, entity_id)
            )
            result = self.cursor.fetchone()

            if result:
                last_update, next_update, status = result
                return {
                    'last_update': datetime.strptime(last_update, '%Y-%m-%d %H:%M:%S') if last_update else None,
                    'next_update': datetime.strptime(next_update, '%Y-%m-%d %H:%M:%S') if next_update else None,
                    'status': status
                }
            return None
        except Exception as e:
            logger.error(f"Error getting update info: {e}")
            return None

    def is_update_needed(self, entity_type, entity_id=None):
        """Проверяет, нужно ли обновлять указанную сущность

        :return: True, если обновление нужно, иначе False
        """
        try:
            update_info = self.get_update_info(entity_type, entity_id)

            # Если нет информации об обновлении, значит обновление нужно
            if not update_info:
                return True

            # Если указано время следующего обновления и оно еще не наступило
            if update_info['next_update'] and update_info['next_update'] > datetime.now():
                return False

            return True
        except Exception as e:
            logger.error(f"Error checking update need: {e}")
            # В случае ошибки лучше обновить чем не обновить
            return True

    def start_update(self, entity_type, entity_id=None):
        """Отмечает начало процесса обновления"""
        try:
            now = datetime.now()

            self.cursor.execute(
                """
                INSERT INTO update_info (entity_type, entity_id, last_update, status)
                VALUES (?, ?, ?, 'in_progress')
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                last_update = excluded.last_update,
                status = excluded.status
                """,
                (entity_type, entity_id, now.strftime('%Y-%m-%d %H:%M:%S'))
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error starting update: {e}")
            return False

    def complete_update(self, entity_type, entity_id=None, next_update=None, status='completed'):
        """Отмечает завершение процесса обновления"""
        try:
            now = datetime.now()
            next_update_str = next_update.strftime('%Y-%m-%d %H:%M:%S') if next_update else None

            self.cursor.execute(
                """
                UPDATE update_info
                SET last_update = ?, next_update = ?, status = ?
                WHERE entity_type = ? AND entity_id = ?
                """,
                (now.strftime('%Y-%m-%d %H:%M:%S'), next_update_str, status, entity_type, entity_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error completing update: {e}")
            return False

    def check_incomplete_updates(self):
        """Проверяет и сбрасывает незавершенные обновления"""
        try:
            self.cursor.execute(
                """
                SELECT entity_type, entity_id 
                FROM update_info 
                WHERE status = 'in_progress'
                """
            )
            incomplete = self.cursor.fetchall()

            for entity_type, entity_id in incomplete:
                logger.warning(f"Found incomplete update for {entity_type} {entity_id}")

                # Сбрасываем статус, чтобы обновление запустилось заново
                self.cursor.execute(
                    """
                    UPDATE update_info
                    SET status = 'interrupted', next_update = NULL
                    WHERE entity_type = ? AND entity_id = ?
                    """,
                    (entity_type, entity_id)
                )

            self.conn.commit()
            return len(incomplete)
        except Exception as e:
            logger.error(f"Error checking incomplete updates: {e}")
            return 0

    def close(self):
        """Закрывает соединение с базой данных"""
        try:
            self.conn.close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")
