import csv
import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, flash
from models import BudgetDatabase, DATABASE_URL

MAX_DESC_LENGTH = 255
CURRENCY_SYMBOL = "$"

app = Flask(__name__)
app.secret_key = "secure_session_key_for_flash_messages"
db = BudgetDatabase(DATABASE_URL)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error_code=404, error_msg="The page you are looking for does not exist."), 404


@app.errorhandler(500)
def internal_server_error(e):
    return render_template('error.html', error_code=500, error_msg="An internal server error occurred."), 500


@app.before_request
def check_initialization():
    if request.endpoint in ['initialize', 'static']:
        return
    settings = db.get_user_settings()
    if not settings or not settings['is_initialized']:
        return redirect(url_for('initialize'))


@app.context_processor
def inject_global_variables():
    settings = db.get_user_settings()
    accounts = db.get_accounts()
    return dict(user_name=settings['user_name'] if settings else "User", accounts=accounts)


def parse_csv_date(date_str):
    date_str = date_str.split(' ')[0].strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%d/%m/%Y', '%m-%d-%Y', '%m-%d-%y'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return date_str


def handle_csv_upload(file, account_name):
    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        csv_reader = csv.reader(stream)

        raw_headers = None
        for row in csv_reader:
            row_str = " ".join([str(c).lower() for c in row])
            if 'date' in row_str and (
                    'amount' in row_str or 'debit' in row_str or 'desc' in row_str or 'memo' in row_str):
                raw_headers = row
                break

        if not raw_headers:
            return 0, "Could not detect valid headers in the CSV."

        normalized_headers = []
        for h in raw_headers:
            h_low = str(h).strip().lower()
            if 'date' in h_low and 'date' not in normalized_headers:
                normalized_headers.append('date')
            elif ('debit' in h_low or 'withdrawal' in h_low) and 'debit' not in normalized_headers:
                normalized_headers.append('debit')
            elif ('credit' in h_low or 'deposit' in h_low) and 'credit' not in normalized_headers:
                normalized_headers.append('credit')
            elif 'amount' in h_low and 'amount' not in normalized_headers:
                normalized_headers.append('amount')
            elif any(x in h_low for x in ['desc', 'memo', 'payee', 'name']) and 'description' not in normalized_headers:
                normalized_headers.append('description')
            elif 'type' in h_low and 'transaction' in h_low and 'type' not in normalized_headers:
                normalized_headers.append('type')
            elif 'category' in h_low and 'category' not in normalized_headers:
                normalized_headers.append('category')
            else:
                normalized_headers.append(h_low)

        is_split_format = 'debit' in normalized_headers or 'credit' in normalized_headers

        if 'date' not in normalized_headers or 'description' not in normalized_headers:
            return 0, "CSV must contain at least a 'Date' and 'Description' column."
        if not is_split_format and 'amount' not in normalized_headers:
            return 0, "CSV must contain an 'Amount' column or split 'Debit/Credit' columns."

        dict_reader = csv.DictReader(stream, fieldnames=normalized_headers)
        imported_count = 0

        for row in dict_reader:
            try:
                date_val = row.get('date', '').strip()
                desc = row.get('description', '').strip()
                if not date_val: continue
                formatted_date = parse_csv_date(date_val)
                category = row.get('category', 'Uncategorized').strip()
                if not category: category = 'Uncategorized'

                if is_split_format:
                    debit_str = row.get('debit', '').replace(',', '').replace('$', '').replace('-', '').strip()
                    credit_str = row.get('credit', '').replace(',', '').replace('$', '').replace('-', '').strip()
                    debit = float(debit_str) if debit_str else 0.0
                    credit = float(credit_str) if credit_str else 0.0

                    if credit > 0:
                        raw_type, raw_amount = 'Deposit', credit
                    elif debit > 0:
                        raw_type, raw_amount = 'Withdrawal', debit
                    else:
                        continue
                else:
                    amt_str = row.get('amount', '').replace(',', '').replace('$', '').strip()
                    if not amt_str: continue
                    raw_amount = float(amt_str)

                    if raw_amount < 0:
                        raw_type, raw_amount = 'Withdrawal', abs(raw_amount)
                    elif raw_amount > 0:
                        raw_type = 'Deposit'
                    else:
                        raw_type = row.get('type', '').strip().capitalize()
                        if raw_type not in ['Deposit', 'Withdrawal']: raw_type = 'Withdrawal'

                db.add_transaction(account_name, raw_type, raw_amount, category, formatted_date, desc[:MAX_DESC_LENGTH])
                imported_count += 1
            except ValueError:
                continue

        if imported_count == 0:
            return 0, "No valid transactions found. Check formatting."
        return imported_count, None
    except Exception as e:
        return 0, f"Error parsing CSV file: {e}"


@app.route('/initialize', methods=['GET', 'POST'])
def initialize():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        timezone = request.form.get('timezone', 'UTC')
        inc_raw, bills_raw = request.form.get('income', ''), request.form.get('bills', '')
        needs_raw, wants_raw, savings_raw = request.form.get('needs_pct', '50'), request.form.get('wants_pct',
                                                                                                  '30'), request.form.get(
            'savings_pct', '20')

        form_data = {
            'name': name, 'income': inc_raw, 'bills': bills_raw,
            'needs_pct': needs_raw, 'wants_pct': wants_raw, 'savings_pct': savings_raw
        }

        try:
            income, bills = float(inc_raw) if inc_raw else 0.0, float(bills_raw) if bills_raw else 0.0
            needs_pct, wants_pct, savings_pct = float(needs_raw), float(wants_raw), float(savings_raw)
            if round(needs_pct + wants_pct + savings_pct) != 100:
                flash("Budget percentages must equal exactly 100%.", "error")
                return render_template('initialize.html', symbol=CURRENCY_SYMBOL, form_data=form_data)
        except ValueError:
            flash("Fields must be valid numbers.", "error")
            return render_template('initialize.html', symbol=CURRENCY_SYMBOL, form_data=form_data)

        files = request.files.getlist('csv_files')
        total_count = 0
        error_msgs = []

        for file in files:
            if file and file.filename != '':
                if file.filename.lower().endswith('.csv'):
                    count, error = handle_csv_upload(file, 'Checking')
                    if error:
                        error_msgs.append(f"{file.filename}: {error}")
                    else:
                        total_count += count
                else:
                    error_msgs.append(f"{file.filename}: Invalid file format.")

        if error_msgs:
            for err in error_msgs:
                flash(err, "error")
            if total_count == 0:
                return render_template('initialize.html', symbol=CURRENCY_SYMBOL, form_data=form_data)

        db.update_initial_settings(name if name else 'User', income, bills, timezone, needs_pct, wants_pct, savings_pct)
        if total_count > 0:
            flash(f"Successfully imported {total_count} transactions!", "success")
        elif not files or not files[0].filename:
            flash("Setup complete!", "success")

        return redirect(url_for('dashboard'))

    return render_template('initialize.html', symbol=CURRENCY_SYMBOL, form_data={})


@app.route('/')
def dashboard():
    settings = db.get_user_settings()
    try:
        local_now = datetime.now(ZoneInfo(settings['timezone']))
    except Exception:
        local_now = datetime.now()

    db.sync_recurring_transactions(local_now.strftime('%Y-%m-%d'))

    requested_month = request.args.get('month')
    search_query = request.args.get('search', '').strip()
    account_filter = request.args.get('account', '').strip()
    page = request.args.get('page', 1, type=int)

    if requested_month:
        try:
            display_dt = datetime.strptime(requested_month, '%Y-%m')
            current_month_prefix = requested_month
            current_month_display = display_dt.strftime('%B %Y')
        except ValueError:
            current_month_prefix = local_now.strftime('%Y-%m')
            current_month_display = local_now.strftime('%B %Y')
    else:
        current_month_prefix = local_now.strftime('%Y-%m')
        current_month_display = local_now.strftime('%B %Y')

    totalIncome, totalExpenses, netBalance, account_balances, bucket_summary, pie_summary = db.get_financial_summary(
        current_month_prefix, account_filter)
    transactions, total_pages, total_items = db.get_paginated_transactions(current_month_prefix, search_query,
                                                                           account_filter, page, per_page=10)
    budget_targets = db.calculate_budget_targets()

    return render_template(
        'dashboard.html',
        income=totalIncome, expenses=totalExpenses, balance=netBalance,
        account_balances=account_balances, bucket_summary=bucket_summary, targets=budget_targets,
        transactions=transactions, symbol=CURRENCY_SYMBOL,
        current_month_display=current_month_display, current_month_prefix=current_month_prefix,
        pie_labels_json=json.dumps(list(pie_summary.keys())),
        pie_data_json=json.dumps(list(pie_summary.values())),
        search_query=search_query, selected_account=account_filter, current_page=page, total_pages=total_pages
    )


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            db.update_user_name(name)
            flash("Profile updated successfully!", "success")
            return redirect(url_for('dashboard'))
        flash("Name cannot be empty.", "error")
    return render_template('profile.html')


@app.route('/add', methods=['GET', 'POST'])
def add_transaction():
    if request.method == 'POST':
        account_name = request.form.get('account_name')
        trans_type, amount = request.form.get('type'), request.form.get('amount')
        category, date = request.form.get('category', 'Uncategorized'), request.form.get('date')
        desc = request.form.get('description', '').strip()

        if not trans_type or not amount or not date or not account_name:
            flash("Required fields are missing.", "error")
            return redirect(url_for('add_transaction'))
        try:
            if float(amount) <= 0: raise ValueError
        except ValueError:
            flash("Amount must be a positive numeric value.", "error")
            return redirect(url_for('add_transaction'))

        db.add_transaction(account_name, trans_type, float(amount), category, date, desc)
        flash("Transaction successfully saved!", "success")
        return redirect(url_for('dashboard'))

    categories = db.get_categories()
    return render_template('add_transaction.html', categories=categories, symbol=CURRENCY_SYMBOL,
                           max_desc=MAX_DESC_LENGTH)


@app.route('/edit/<int:tx_id>', methods=['GET', 'POST'])
def edit_transaction(tx_id):
    transaction = db.get_transaction(tx_id)
    if not transaction: return redirect(url_for('dashboard'))

    if request.method == 'POST':
        account_name = request.form.get('account_name')
        trans_type, amount = request.form.get('type'), request.form.get('amount')
        category, date = request.form.get('category', 'Uncategorized'), request.form.get('date')
        desc = request.form.get('description', '').strip()

        try:
            if float(amount) <= 0: raise ValueError
            db.update_transaction(tx_id, account_name, trans_type, float(amount), category, date, desc)
            flash("Transaction updated!", "success")
            return redirect(url_for('dashboard'))
        except ValueError:
            flash("Invalid amount.", "error")

    categories = db.get_categories()
    return render_template('edit_transaction.html', tx=transaction, categories=categories, symbol=CURRENCY_SYMBOL,
                           max_desc=MAX_DESC_LENGTH)


@app.route('/delete/<int:tx_id>', methods=['POST'])
def delete_transaction(tx_id):
    db.delete_transaction(tx_id)
    flash("Transaction deleted successfully.", "success")
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        files = request.files.getlist('csv_files')
        if files and files[0].filename != '':
            account_name = request.form.get('import_account', 'Checking')
            total_count = 0
            error_msgs = []

            for file in files:
                if file.filename.lower().endswith('.csv'):
                    count, err = handle_csv_upload(file, account_name)
                    if err:
                        error_msgs.append(f"{file.filename}: {err}")
                    else:
                        total_count += count
                else:
                    error_msgs.append(f"{file.filename}: Invalid format. Must be .csv")

            for err in error_msgs:
                flash(err, "error")
            if total_count > 0:
                flash(f"Successfully imported {total_count} transactions to {account_name}!", "success")
            return redirect(url_for('settings'))

        else:
            try:
                inc, bills = float(request.form.get('income')), float(request.form.get('bills'))
                needs, wants, savings = float(request.form.get('needs_pct')), float(
                    request.form.get('wants_pct')), float(request.form.get('savings_pct'))
                if inc < 0 or bills < 0: raise ValueError
                if round(needs + wants + savings) != 100:
                    flash("Budget percentages must sum to 100%.", "error")
                    return redirect(url_for('settings'))

                db.update_user_settings(inc, bills, needs, wants, savings)
                flash("Settings updated successfully!", "success")
                return redirect(url_for('dashboard'))
            except ValueError:
                flash("Please enter valid numeric values.", "error")
                return redirect(url_for('settings'))

    return render_template('settings.html', settings=db.get_user_settings(), symbol=CURRENCY_SYMBOL,
                           categories=db.get_categories(), recurrings=db.get_recurring())


@app.route('/account/add', methods=['POST'])
def add_account():
    name = request.form.get('name', '').strip()
    if name:
        if db.add_account(name):
            flash("Account added.", "success")
        else:
            flash("Account already exists.", "error")
    return redirect(url_for('settings'))


@app.route('/account/delete/<int:acc_id>', methods=['POST'])
def delete_account(acc_id):
    db.delete_account(acc_id)
    flash("Account deleted.", "success")
    return redirect(url_for('settings'))


@app.route('/category/add', methods=['POST'])
def add_category():
    name = request.form.get('name', '').strip()
    trans_type = request.form.get('type')
    bucket = request.form.get('bucket')
    if name and trans_type and bucket:
        if db.add_category(name, trans_type, bucket):
            flash("Category added.", "success")
        else:
            flash("Category already exists.", "error")
    return redirect(url_for('settings'))


@app.route('/category/delete/<int:cat_id>', methods=['POST'])
def delete_category(cat_id):
    db.delete_category(cat_id)
    flash("Category deleted.", "success")
    return redirect(url_for('settings'))


@app.route('/recurring/add', methods=['POST'])
def add_recurring():
    acc = request.form.get('account_name')
    trans_type, amount = request.form.get('type'), request.form.get('amount')
    category, next_date = request.form.get('category'), request.form.get('next_date')
    desc = request.form.get('description', '').strip()
    try:
        if db.add_recurring(acc, trans_type, float(amount), category, desc, next_date):
            flash("Recurring transaction created.", "success")
        else:
            flash("Error creating recurring transaction.", "error")
    except ValueError:
        flash("Invalid data for recurring.", "error")
    return redirect(url_for('settings'))


@app.route('/recurring/delete/<int:rec_id>', methods=['POST'])
def delete_recurring(rec_id):
    db.delete_recurring(rec_id)
    flash("Recurring transaction canceled.", "success")
    return redirect(url_for('settings'))


@app.route('/reports')
def reports():
    months, incomes, expenses = db.get_monthly_trends()
    return render_template('reports.html', months_json=json.dumps(months), incomes_json=json.dumps(incomes),
                           expenses_json=json.dumps(expenses))


if __name__ == '__main__':
    app.run(debug=True, port=5000)