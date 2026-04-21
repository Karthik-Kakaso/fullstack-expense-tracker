import os 
from flask import Flask, render_template, request, redirect, Response
from datetime import date, timedelta
import sqlite3
import csv
import io

app = Flask(__name__)
def get_db_connection():
    # This tells the server exactly which folder app.py is sitting in
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    # This safely connects that folder to your database file
    db_path = os.path.join(BASE_DIR, 'database', 'expenses.db')
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def home():
    conn = get_db_connection()
    
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)

    today_str = today.strftime('%Y-%m-%d')
    week_str = start_of_week.strftime('%Y-%m-%d')
    month_str = start_of_month.strftime('%Y-%m-%d')

    daily_total = conn.execute('SELECT SUM(amount) FROM expenses WHERE date = ?', (today_str,)).fetchone()[0] or 0
    weekly_total = conn.execute('SELECT SUM(amount) FROM expenses WHERE date >= ?', (week_str,)).fetchone()[0] or 0
    monthly_total = conn.execute('SELECT SUM(amount) FROM expenses WHERE date >= ?', (month_str,)).fetchone()[0] or 0
    
    # --- Budget Tracking Logic ---
    category_data = conn.execute('''
        SELECT c.name, c.monthly_limit, 
               COALESCE(SUM(e.amount), 0) as spent
        FROM categories c
        LEFT JOIN expenses e ON c.id = e.category_id AND e.date >= ?
        GROUP BY c.id
    ''', (month_str,)).fetchall()

    budgets = []
    for row in category_data:
        limit = row['monthly_limit']
        spent = row['spent']
        percentage = (spent / limit) * 100 if limit > 0 else 0
        remaining = limit - spent
        
        if spent > limit:
            status, color_class = 'Over budget', 'danger'
        elif percentage >= 85:
            status, color_class = 'Near limit', 'warning'
        else:
            status, color_class = 'On track', 'success'
            
        budgets.append({
            'name': row['name'], 'limit': limit, 'spent': spent,
            'percentage': min(percentage, 100),
            'remaining': remaining, 'remaining_abs': abs(remaining),
            'status': status, 'color_class': color_class
        })

    # --- Advanced Filter Engine ---
    search_query = request.args.get('q', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = '''
        SELECT expenses.id, expenses.amount, expenses.date, expenses.note, categories.name AS category_name
        FROM expenses 
        JOIN categories ON expenses.category_id = categories.id
        WHERE 1=1
    '''
    params = []

    if search_query:
        query += ' AND (expenses.note LIKE ? OR categories.name LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%'])
    if start_date:
        query += ' AND expenses.date >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND expenses.date <= ?'
        params.append(end_date)

    query += ' ORDER BY expenses.date DESC'
    expenses = conn.execute(query, params).fetchall()
    
    # --- THE MISSING LINES ARE BACK ---
    categories = conn.execute('SELECT * FROM categories').fetchall()
    total_result = conn.execute('SELECT SUM(amount) FROM expenses').fetchone()[0]
    total_amount = total_result if total_result is not None else 0
    chart_data = conn.execute('''
        SELECT categories.name, SUM(expenses.amount) as total
        FROM expenses JOIN categories ON expenses.category_id = categories.id GROUP BY categories.name
    ''').fetchall()
   # --- NEW: Stacked Bar Chart Data ---
    monthly_chart_data = conn.execute('''
        SELECT strftime('%Y-%m', expenses.date) as month, categories.name as category, SUM(expenses.amount) as total
        FROM expenses
        JOIN categories ON expenses.category_id = categories.id
        GROUP BY month, category
        ORDER BY month ASC
    ''').fetchall()
    
    conn.close()

    return render_template('index.html', expenses=expenses, categories=categories, 
                           total=total_amount, chart_data=chart_data,
                           daily=daily_total, weekly=weekly_total, monthly=monthly_total,
                           budgets=budgets, monthly_chart_data=monthly_chart_data)

@app.route('/add', methods=['POST'])
def add_expense():
    amount = request.form['amount']
    category_id = request.form['category_id']
    date = request.form['date']
    note = request.form['note']

    conn = get_db_connection()
    conn.execute(
        'INSERT INTO expenses (user_id, category_id, amount, date, note) VALUES (?, ?, ?, ?, ?)',
        (1, category_id, amount, date, note)
    )
    conn.commit()
    conn.close()

    return redirect('/')

@app.route('/set_budget', methods=['POST'])
def set_budget():
    category_id = request.form['category_id']
    new_limit = request.form['limit']

    conn = get_db_connection()
    # Update the monthly_limit for the specific category
    conn.execute('UPDATE categories SET monthly_limit = ? WHERE id = ?', (new_limit, category_id))
    conn.commit()
    conn.close()

    return redirect('/')

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit_expense(id):
    conn = get_db_connection()

    if request.method == 'POST':
        amount = request.form['amount']
        category_id = request.form['category_id']
        date = request.form['date']
        note = request.form['note']

        conn.execute('''
            UPDATE expenses
            SET category_id = ?, amount = ?, date = ?, note = ?
            WHERE id = ?
        ''', (category_id, amount, date, note, id))
        conn.commit()
        conn.close()
        return redirect('/')

    expense = conn.execute('SELECT * FROM expenses WHERE id = ?', (id,)).fetchone()
    categories = conn.execute('SELECT * FROM categories').fetchall()
    conn.close()

    return render_template('edit.html', expense=expense, categories=categories)

@app.route('/delete/<int:id>')
def delete_expense(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM expenses WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect('/')

@app.route('/export')
def export_csv():
    # 1. Grab the current filters from the URL
    search_query = request.args.get('q', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_db_connection()
    query = '''
        SELECT expenses.id, expenses.amount, expenses.date, expenses.note, categories.name AS category_name
        FROM expenses 
        JOIN categories ON expenses.category_id = categories.id
        WHERE 1=1
    '''
    params = []

    if search_query:
        query += ' AND (expenses.note LIKE ? OR categories.name LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%'])
    if start_date:
        query += ' AND expenses.date >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND expenses.date <= ?'
        params.append(end_date)

    query += ' ORDER BY expenses.date DESC'
    expenses = conn.execute(query, params).fetchall()
    conn.close()

    # 2. Create the CSV file in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write the column headers
    writer.writerow(['ID', 'Amount (INR)', 'Category', 'Date', 'Note'])
    
    # Write the actual data rows
    for exp in expenses:
        writer.writerow([exp['id'], exp['amount'], exp['category_name'], exp['date'], exp['note']])
    
    # 3. Send it to the browser as a downloaded file
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=my_expenses.csv"}
    )

if __name__ == "__main__":
    app.run(debug=True, port=3000)