import pytest
import io
from app import app
from models import BudgetDatabase


# Fixture for lightning-fast database testing
@pytest.fixture
def db(tmp_path):
    # Using pytest's built-in tmp_path creates an isolated, temporary physical file.
    # We cannot use ":memory:" here because SQLite creates a brand new, empty
    # database for every single connection, losing our initialized tables!
    db_file = tmp_path / "test_budget.db"
    test_db = BudgetDatabase(str(db_file))
    yield test_db


# Fixture for testing Flask routes
@pytest.fixture
def client(db):
    # Override the app's database with our temporary test database
    app.config['TESTING'] = True
    import app as my_app
    my_app.db = db

    # Mock the initialization so the @app.before_request hook doesn't block our tests
    db.update_initial_settings('Test User', 5000, 2000, 'UTC', 50, 30, 20)

    with app.test_client() as client:
        yield client


def test_accounts_and_soft_delete(db):
    db.add_account("Business Checking")
    accounts = [a['name'] for a in db.get_accounts()]
    assert "Business Checking" in accounts

    # Test our new soft delete logic
    acc_id = next(a['id'] for a in db.get_accounts() if a['name'] == "Business Checking")
    db.delete_account(acc_id)
    updated_accounts = [a['name'] for a in db.get_accounts()]
    assert "Business Checking" not in updated_accounts


def test_custom_categories(db):
    db.add_category("Gym", "Withdrawal", "Wants")
    db.add_transaction("Checking", "Withdrawal", 50.0, "Gym", "2026-07-24", "Monthly fee")
    _, _, _, _, bucket_summary, _ = db.get_financial_summary()
    assert bucket_summary["Wants"] == 50.0

    categories = db.get_categories()
    gym_cat_id = next(c['id'] for c in categories if c['name'] == "Gym")
    db.delete_category(gym_cat_id)
    tx = db.get_transaction(1)
    assert tx['category'] == "Uncategorized"


def test_pagination_logic(db):
    for i in range(15):
        db.add_transaction("Checking", "Withdrawal", 10.0, "Groceries", f"2026-07-{i + 1:02d}", f"Item {i}")

    items, total_pages, total_items = db.get_paginated_transactions(None, "", None, page=1, per_page=10)
    assert len(items) == 10
    assert total_pages == 2
    assert total_items == 15


def test_recurring_logic_rollover(db):
    db.add_recurring("Checking", "Withdrawal", 100.0, "Rent", "Home", "2026-01-31")
    db.sync_recurring_transactions("2026-02-05")
    items, _, _ = db.get_paginated_transactions(None, "", None, 1, 10)
    assert len(items) == 1

    recurring = db.get_recurring()
    assert recurring[0]['nextDate'] == "2026-02-28"

    db.sync_recurring_transactions("2026-03-05")
    recurring_updated = db.get_recurring()
    assert recurring_updated[0]['nextDate'] == "2026-03-31"


def test_dashboard_route(client):
    # Test that the dashboard loads successfully (HTTP 200 OK)
    response = client.get('/')
    assert response.status_code == 200
    assert b"Total Net Balance" in response.data


def test_add_transaction_route(client, db):
    # Simulate submitting the /add form
    form_data = {
        'account_name': 'Checking',
        'type': 'Withdrawal',
        'amount': '45.00',
        'category': 'Groceries',
        'date': '2026-07-25',
        'description': 'Trader Joe\'s'
    }

    # follow_redirects=True tells the test client to follow the redirect back to the dashboard
    response = client.post('/add', data=form_data, follow_redirects=True)
    assert response.status_code == 200

    # Verify the transaction actually made it into the database
    items, _, _ = db.get_paginated_transactions(None, "", None, 1, 10)
    assert len(items) == 1
    assert items[0]['description'] == "Trader Joe's"
    assert items[0]['transactionAmount'] == 45.0


def test_csv_upload_route(client, db):
    # Create a mock CSV file in memory
    csv_content = b"Date,Description,Amount\n07/25/2026,Internet Bill,-80.00\n07/26/2026,Salary,2000.00"
    mock_file = (io.BytesIO(csv_content), 'test_statement.csv')

    # Simulate submitting the CSV upload form on the settings page
    form_data = {
        'import_account': 'Checking',
        'csv_files': mock_file
    }

    response = client.post('/settings', data=form_data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200

    # Verify the database processed the CSV correctly and inserted the 2 rows
    items, _, _ = db.get_paginated_transactions(None, "", None, 1, 10)
    assert len(items) == 2

    # Check specific values to ensure the CSV parser correctly extracted the data
    descriptions = [item['description'] for item in items]
    assert "Internet Bill" in descriptions
    assert "Salary" in descriptions