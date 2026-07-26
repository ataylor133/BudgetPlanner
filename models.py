import sqlite3
import calendar
from datetime import datetime

DATABASE_URL = "budget_planner.db"


class BudgetDatabase:
    """
    Handles secure database connections, reads, writes, and core business logic.
    Maintains a strict separation from the GUI routing.
    """

    def __init__(self, db_url):
        self.db_url = db_url
        self._initialize_database()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_url)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_database(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Updated to include is_active for soft deletions
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    is_active INTEGER DEFAULT 1
                )
            ''')

            # Migration: Add is_active column to existing databases
            cursor.execute("PRAGMA table_info(accounts)")
            columns = [col['name'] for col in cursor.fetchall()]
            if 'is_active' not in columns:
                cursor.execute("ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT DEFAULT 'Checking',
                    transactionType TEXT NOT NULL,
                    transactionAmount REAL NOT NULL,
                    category TEXT,
                    transactionDate TEXT NOT NULL,
                    description TEXT
                )
            ''')

            cursor.execute("PRAGMA table_info(transactions)")
            columns = [col['name'] for col in cursor.fetchall()]
            if 'account_name' not in columns:
                cursor.execute("ALTER TABLE transactions ADD COLUMN account_name TEXT DEFAULT 'Checking'")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    user_name TEXT DEFAULT 'User',
                    monthly_income REAL DEFAULT 0,
                    monthly_bills REAL DEFAULT 0,
                    timezone TEXT DEFAULT 'UTC',
                    is_initialized INTEGER DEFAULT 0,
                    needs_pct REAL DEFAULT 50,
                    wants_pct REAL DEFAULT 30,
                    savings_pct REAL DEFAULT 20
                )
            ''')
            cursor.execute('INSERT OR IGNORE INTO settings (id) VALUES (1)')

            cursor.execute("PRAGMA table_info(settings)")
            settings_columns = [col['name'] for col in cursor.fetchall()]
            if 'needs_pct' not in settings_columns:
                cursor.execute("ALTER TABLE settings ADD COLUMN needs_pct REAL DEFAULT 50")
                cursor.execute("ALTER TABLE settings ADD COLUMN wants_pct REAL DEFAULT 30")
                cursor.execute("ALTER TABLE settings ADD COLUMN savings_pct REAL DEFAULT 20")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    trans_type TEXT NOT NULL,
                    bucket TEXT NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS recurring_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT DEFAULT 'Checking',
                    transactionType TEXT NOT NULL,
                    transactionAmount REAL NOT NULL,
                    category TEXT,
                    description TEXT,
                    nextDate TEXT NOT NULL,
                    originalDay INTEGER NOT NULL DEFAULT 1
                )
            ''')

            cursor.execute("PRAGMA table_info(recurring_transactions)")
            rec_columns = [col['name'] for col in cursor.fetchall()]
            if 'account_name' not in rec_columns:
                cursor.execute("ALTER TABLE recurring_transactions ADD COLUMN account_name TEXT DEFAULT 'Checking'")

            cursor.execute('SELECT COUNT(*) as count FROM accounts')
            if cursor.fetchone()['count'] == 0:
                cursor.execute("INSERT INTO accounts (name) VALUES ('Checking')")
                cursor.execute("INSERT INTO accounts (name) VALUES ('Savings')")

            cursor.execute('SELECT COUNT(*) as count FROM categories')
            if cursor.fetchone()['count'] == 0:
                default_categories = [
                    ('Salary', 'Deposit', 'Income Source'), ('Rent', 'Withdrawal', 'Needs'),
                    ('Groceries', 'Withdrawal', 'Needs'), ('Dining Out', 'Withdrawal', 'Wants'),
                    ('Emergency Fund', 'Withdrawal', 'Savings'), ('Uncategorized', 'Withdrawal', 'Wants')
                ]
                cursor.executemany('INSERT INTO categories (name, trans_type, bucket) VALUES (?, ?, ?)',
                                   default_categories)

            conn.commit()

    def get_accounts(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Only return active accounts for UI dropdowns
            cursor.execute('SELECT * FROM accounts WHERE is_active = 1 ORDER BY name')
            return cursor.fetchall()

    def add_account(self, name):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Check if account exists (active or inactive)
                cursor.execute('SELECT id, is_active FROM accounts WHERE name = ?', (name,))
                acc = cursor.fetchone()

                if acc:
                    if acc['is_active'] == 0:
                        # Reactivate the soft-deleted account
                        cursor.execute('UPDATE accounts SET is_active = 1 WHERE id = ?', (acc['id'],))
                        conn.commit()
                        return True
                    else:
                        # Account already exists and is active
                        return False

                # Account is entirely new
                cursor.execute('INSERT INTO accounts (name) VALUES (?)', (name,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def delete_account(self, account_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Soft delete: Flag as inactive instead of wiping data
                cursor.execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (account_id,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def get_categories(self, trans_type=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if trans_type:
                cursor.execute('SELECT * FROM categories WHERE trans_type = ? ORDER BY name', (trans_type,))
            else:
                cursor.execute('SELECT * FROM categories ORDER BY name')
            return cursor.fetchall()

    def add_category(self, name, trans_type, bucket):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO categories (name, trans_type, bucket) VALUES (?, ?, ?)',
                               (name, trans_type, bucket))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def delete_category(self, cat_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT name FROM categories WHERE id = ?', (cat_id,))
                cat = cursor.fetchone()

                if cat:
                    cursor.execute("UPDATE transactions SET category = 'Uncategorized' WHERE category = ?",
                                   (cat['name'],))

                cursor.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def add_recurring(self, account_name, trans_type, amount, category, desc, next_date):
        try:
            original_day = datetime.strptime(next_date, '%Y-%m-%d').day
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO recurring_transactions (account_name, transactionType, transactionAmount, category, description, nextDate, originalDay)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (account_name, trans_type, amount, category, desc, next_date, original_day))
                conn.commit()
                return True
        except (sqlite3.Error, ValueError):
            return False

    def get_recurring(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recurring_transactions ORDER BY nextDate ASC')
            return cursor.fetchall()

    def delete_recurring(self, rec_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM recurring_transactions WHERE id = ?', (rec_id,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def sync_recurring_transactions(self, current_date_str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM recurring_transactions WHERE nextDate <= ?', (current_date_str,))
            due_transactions = cursor.fetchall()

            for rec in due_transactions:
                cursor.execute('''
                    INSERT INTO transactions (account_name, transactionType, transactionAmount, category, transactionDate, description)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (rec['account_name'], rec['transactionType'], rec['transactionAmount'], rec['category'],
                      rec['nextDate'], rec['description']))

                due_dt = datetime.strptime(rec['nextDate'], '%Y-%m-%d')
                month, year, original_day = due_dt.month, due_dt.year, rec['originalDay']

                if month == 12:
                    month = 1
                    year += 1
                else:
                    month += 1

                day = min(original_day, calendar.monthrange(year, month)[1])
                next_dt_str = f"{year}-{month:02d}-{day:02d}"

                cursor.execute('UPDATE recurring_transactions SET nextDate = ? WHERE id = ?', (next_dt_str, rec['id']))
            conn.commit()

    def get_user_settings(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM settings WHERE id = 1')
            return cursor.fetchone()

    def update_initial_settings(self, name, income, bills, timezone, needs_pct, wants_pct, savings_pct):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE settings 
                    SET user_name = ?, monthly_income = ?, monthly_bills = ?, timezone = ?, 
                        needs_pct = ?, wants_pct = ?, savings_pct = ?, is_initialized = 1
                    WHERE id = 1
                ''', (name, income, bills, timezone, needs_pct, wants_pct, savings_pct))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def update_user_settings(self, income, bills, needs_pct, wants_pct, savings_pct):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE settings 
                    SET monthly_income = ?, monthly_bills = ?, needs_pct = ?, wants_pct = ?, savings_pct = ? 
                    WHERE id = 1
                ''', (income, bills, needs_pct, wants_pct, savings_pct))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def update_user_name(self, name):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE settings SET user_name = ? WHERE id = 1', (name,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def calculate_budget_targets(self):
        settings = self.get_user_settings()
        income = settings['monthly_income']
        bills = settings['monthly_bills']
        custom_needs, custom_wants, custom_savings = settings['needs_pct'], settings['wants_pct'], settings[
            'savings_pct']

        if income <= 0:
            return {"Needs": (0, 0), "Wants": (0, 0), "Savings": (0, 0)}

        needs_pct, wants_pct, savings_pct = custom_needs, custom_wants, custom_savings
        bills_pct = (bills / income) * 100

        if bills_pct > custom_needs:
            needs_pct = bills_pct
            remaining_pct = 100.0 - needs_pct
            if remaining_pct < 0:
                needs_pct, wants_pct, savings_pct = 100.0, 0.0, 0.0
            else:
                ratio_total = custom_wants + custom_savings
                if ratio_total > 0:
                    wants_pct = remaining_pct * (custom_wants / ratio_total)
                    savings_pct = remaining_pct * (custom_savings / ratio_total)
                else:
                    wants_pct, savings_pct = 0.0, 0.0

        return {
            "Needs": (needs_pct, income * (needs_pct / 100)),
            "Wants": (wants_pct, income * (wants_pct / 100)),
            "Savings": (savings_pct, income * (savings_pct / 100))
        }

    def add_transaction(self, account_name, trans_type, amount, category, date, desc):
        category = category if category else 'Uncategorized'
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO transactions (account_name, transactionType, transactionAmount, category, transactionDate, description)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (account_name, trans_type, amount, category, date, desc))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def get_transaction(self, tx_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM transactions WHERE id = ?', (tx_id,))
            return cursor.fetchone()

    def update_transaction(self, tx_id, account_name, trans_type, amount, category, date, desc):
        category = category if category else 'Uncategorized'
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE transactions 
                    SET account_name = ?, transactionType = ?, transactionAmount = ?, category = ?, transactionDate = ?, description = ?
                    WHERE id = ?
                ''', (account_name, trans_type, amount, category, date, desc, tx_id))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def delete_transaction(self, tx_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM transactions WHERE id = ?', (tx_id,))
                conn.commit()
                return True
        except sqlite3.Error:
            return False

    def get_financial_summary(self, target_month_prefix=None, account_filter=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT name, bucket FROM categories')
            cat_map = {row['name']: row['bucket'] for row in cursor.fetchall()}

            # Fetch active accounts to filter the dashboard cards
            cursor.execute('SELECT name FROM accounts WHERE is_active = 1')
            active_accounts = {row['name'] for row in cursor.fetchall()}

            query = 'SELECT account_name, transactionType, transactionAmount, category FROM transactions WHERE 1=1'
            params = []

            if target_month_prefix:
                query += ' AND substr(transactionDate, 1, 7) = ?'
                params.append(target_month_prefix)
            if account_filter:
                query += ' AND account_name = ?'
                params.append(account_filter)

            cursor.execute(query, params)
            records = cursor.fetchall()

            cursor.execute('SELECT account_name, transactionType, transactionAmount FROM transactions')
            all_records = cursor.fetchall()

        totalIncome, totalExpenses = 0.0, 0.0
        bucket_summary = {"Needs": 0.0, "Wants": 0.0, "Savings": 0.0}
        pie_summary = {}

        for row in records:
            amt = row['transactionAmount']
            cat = row['category']

            if row['transactionType'] == 'Deposit':
                totalIncome += amt
            elif row['transactionType'] == 'Withdrawal':
                totalExpenses += amt
                bucket = cat_map.get(cat, 'Wants')
                if bucket in bucket_summary: bucket_summary[bucket] += amt
                pie_summary[cat] = pie_summary.get(cat, 0.0) + amt

        account_balances = {}
        for row in all_records:
            acc = row['account_name']
            amt = row['transactionAmount']
            if acc not in account_balances: account_balances[acc] = 0.0

            if row['transactionType'] == 'Deposit':
                account_balances[acc] += amt
            elif row['transactionType'] == 'Withdrawal':
                account_balances[acc] -= amt

        total_net_balance = sum(account_balances.values())

        # Render cards for active accounts, OR any deleted account that still retains a non-zero balance
        display_balances = {acc: bal for acc, bal in account_balances.items() if acc in active_accounts or bal != 0}

        return totalIncome, totalExpenses, total_net_balance, display_balances, bucket_summary, pie_summary

    def get_paginated_transactions(self, target_month_prefix, search_query, account_filter, page, per_page):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Utilizing a Common Table Expression (CTE) with an O(N) Window Function for the running balance
            query = """
                WITH RankedTransactions AS (
                    SELECT *, 
                           SUM(CASE WHEN transactionType='Deposit' THEN transactionAmount ELSE -transactionAmount END) 
                           OVER (ORDER BY transactionDate ASC, id ASC) AS running_balance
                    FROM transactions
                    WHERE 1=1
            """

            params = []
            count_params = []

            # We apply the account filter inside the CTE so the running balance correctly tracks the isolated account
            if account_filter:
                query += " AND account_name = ?"
                params.append(account_filter)
                count_params.append(account_filter)

            query += """
                )
                SELECT * FROM RankedTransactions WHERE 1=1
            """

            # We apply the month and search filters in the outer query so the running balance calculates historically
            if target_month_prefix:
                query += " AND substr(transactionDate, 1, 7) = ?"
                params.append(target_month_prefix)
                count_params.append(target_month_prefix)

            if search_query:
                query += " AND (description LIKE ? OR category LIKE ?)"
                params.extend([f"%{search_query}%", f"%{search_query}%"])
                count_params.extend([f"%{search_query}%", f"%{search_query}%"])

            # Count Query
            count_query = "SELECT COUNT(*) as total FROM transactions WHERE 1=1"

            if account_filter:
                count_query += " AND account_name = ?"
            if target_month_prefix:
                count_query += " AND substr(transactionDate, 1, 7) = ?"
            if search_query:
                count_query += " AND (description LIKE ? OR category LIKE ?)"

            cursor.execute(count_query, count_params)
            total_items = cursor.fetchone()['total']

            query += " ORDER BY transactionDate DESC, id DESC LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])

            cursor.execute(query, params)
            items = cursor.fetchall()

            total_pages = (total_items + per_page - 1) // per_page
            return items, total_pages, total_items

    def get_monthly_trends(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT substr(transactionDate, 1, 7) as month, transactionType, SUM(transactionAmount) as total 
                FROM transactions GROUP BY month, transactionType ORDER BY month ASC
            ''')
            records = cursor.fetchall()

        trends = {}
        for row in records:
            m, t_type, total = row['month'], row['transactionType'], row['total']
            if m not in trends: trends[m] = {'Deposit': 0.0, 'Withdrawal': 0.0}
            trends[m][t_type] += total

        return list(trends.keys()), [trends[m]['Deposit'] for m in trends], [trends[m]['Withdrawal'] for m in trends]