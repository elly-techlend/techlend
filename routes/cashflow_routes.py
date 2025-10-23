from flask import Blueprint, render_template, request, session, jsonify
from flask_login import login_required, current_user
from models import CashbookEntry, OtherIncome, Branch
from datetime import datetime, timedelta
from calendar import month_name
from utils.decorators import roles_required

cashflow_bp = Blueprint('cashflow', __name__)

def pct_change(current, previous):
    return round(((current - previous) / previous * 100), 2) if previous else None

@cashflow_bp.route('/cash-flow')
@login_required
@roles_required('Admin')
def view_cash_flow():
    # --- Filters ---
    filter_type = request.args.get('filter')
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    today = datetime.today().date()
    start, end = None, None

    # Determine date range
    if filter_type == 'daily':
        start = end = today
    elif filter_type == 'weekly':
        start = today - timedelta(days=7)
        end = today
    elif filter_type == 'monthly' and month and year:
        start = datetime(year, month, 1).date()
        end = datetime(year, month+1, 1).date() - timedelta(days=1) if month < 12 else datetime(year, 12, 31).date()
    elif filter_type == 'yearly' and year:
        start = datetime(year, 1, 1).date()
        end = datetime(year, 12, 31).date()
    else:
        start = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None

    # --- Queries ---
    cashbook_query = CashbookEntry.query.filter(CashbookEntry.company_id == current_user.company_id)
    income_query = OtherIncome.query.filter(
        OtherIncome.company_id == current_user.company_id,
        OtherIncome.is_active == True
    )

    # Branch filter for non-superuser
    branch_id = session.get('active_branch_id')
    if not current_user.is_superuser and branch_id:
        cashbook_query = cashbook_query.filter(CashbookEntry.branch_id == branch_id)
        income_query = income_query.filter(OtherIncome.branch_id == branch_id)

    # Apply date filters
    if start:
        cashbook_query = cashbook_query.filter(CashbookEntry.date >= start)
        income_query = income_query.filter(OtherIncome.income_date >= start)
    if end:
        cashbook_query = cashbook_query.filter(CashbookEntry.date <= end)
        income_query = income_query.filter(OtherIncome.income_date <= end)

    cashbook_entries = cashbook_query.order_by(CashbookEntry.date.desc()).all()
    other_income_entries = income_query.order_by(OtherIncome.income_date.desc()).all()

    # Totals
    total_in = sum(entry.credit for entry in cashbook_entries) + sum(i.amount for i in other_income_entries)
    total_out = sum(entry.debit for entry in cashbook_entries)
    net_flow = total_in - total_out

    # Previous period for % change
    period_days = (end - start).days + 1 if start and end else 7
    prev_start = start - timedelta(days=period_days) if start else today - timedelta(days=period_days*2)
    prev_end = start - timedelta(days=1) if start else today - timedelta(days=period_days+1)

    prev_cashbook = CashbookEntry.query.filter(
        CashbookEntry.company_id == current_user.company_id,
        CashbookEntry.date >= prev_start,
        CashbookEntry.date <= prev_end
    ).all()
    prev_income = OtherIncome.query.filter(
        OtherIncome.company_id == current_user.company_id,
        OtherIncome.is_active == True,
        OtherIncome.income_date >= prev_start,
        OtherIncome.income_date <= prev_end
    ).all()

    prev_in = sum(entry.credit for entry in prev_cashbook) + sum(i.amount for i in prev_income)
    prev_out = sum(entry.debit for entry in prev_cashbook)

    # --- Safe change variables ---
    in_change = pct_change(total_in, prev_in)
    out_change = pct_change(total_out, prev_out)
    net_change = pct_change(net_flow, prev_in - prev_out)

    # --- Chart data ---
    daily_data = {}
    for entry in cashbook_entries:
        date_str = entry.date.strftime('%Y-%m-%d')
        daily_data.setdefault(date_str, {'in':0,'out':0,'net':0})
        daily_data[date_str]['in'] += entry.credit
        daily_data[date_str]['out'] += entry.debit
        daily_data[date_str]['net'] = daily_data[date_str]['in'] - daily_data[date_str]['out']

    for item in other_income_entries:
        date_str = item.income_date.strftime('%Y-%m-%d')
        daily_data.setdefault(date_str, {'in':0,'out':0,'net':0})
        daily_data[date_str]['in'] += item.amount
        daily_data[date_str]['net'] = daily_data[date_str]['in'] - daily_data[date_str]['out']

    chart_dates = sorted(daily_data.keys())
    chart_inflows = [daily_data[d]['in'] for d in chart_dates]
    chart_outflows = [daily_data[d]['out'] for d in chart_dates]
    chart_net = [daily_data[d]['net'] for d in chart_dates]

    # Months & years for dropdown
    months = list(enumerate(month_name))[1:]
    years = list(range(today.year-5, today.year+1))

    return render_template(
        'cashflow/view_cashflow.html',
        cashbook=cashbook_entries,
        income=other_income_entries,
        total_in=total_in,
        total_out=total_out,
        net_flow=net_flow,
        in_change=in_change,
        out_change=out_change,
        net_change=net_change,
        branch_id=branch_id,
        filter_type=filter_type,
        selected_month=month,
        selected_year=year,
        months=months,
        years=years,
        chart_dates=chart_dates,
        chart_inflows=chart_inflows,
        chart_outflows=chart_outflows,
        chart_net=chart_net
    )

from flask import jsonify

@cashflow_bp.route('/cash-flow/data')
@login_required
@roles_required('Admin')
def cash_flow_data():
    filter_type = request.args.get('filter')
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)

    today = datetime.today().date()
    start, end = None, None

    if filter_type == 'daily':
        start = end = today
    elif filter_type == 'weekly':
        start = today - timedelta(days=7)
        end = today
    elif filter_type == 'monthly' and month and year:
        start = datetime(year, month, 1).date()
        if month == 12:
            end = datetime(year, 12, 31).date()
        else:
            end = (datetime(year, month+1, 1) - timedelta(days=1)).date()
    elif filter_type == 'yearly' and year:
        start = datetime(year, 1, 1).date()
        end = datetime(year, 12, 31).date()
    else:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        start = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None

    # Queries
    cashbook_entries = CashbookEntry.query.filter(
        CashbookEntry.company_id == current_user.company_id
    )
    income_entries = OtherIncome.query.filter(
        OtherIncome.company_id == current_user.company_id,
        OtherIncome.is_active == True
    )

    # Branch filter
    branch_id = session.get('active_branch_id')
    if not current_user.is_superuser and branch_id:
        cashbook_entries = cashbook_entries.filter(CashbookEntry.branch_id == branch_id)
        income_entries = income_entries.filter(OtherIncome.branch_id == branch_id)

    # Date filter
    if start:
        cashbook_entries = cashbook_entries.filter(CashbookEntry.date >= start)
        income_entries = income_entries.filter(OtherIncome.income_date >= start)
    if end:
        cashbook_entries = cashbook_entries.filter(CashbookEntry.date <= end)
        income_entries = income_entries.filter(OtherIncome.income_date <= end)

    cashbook_entries = cashbook_entries.order_by(CashbookEntry.date.desc()).all()
    income_entries = income_entries.order_by(OtherIncome.income_date.desc()).all()

    # Totals
    total_in = sum(entry.credit for entry in cashbook_entries) + sum(i.amount for i in income_entries)
    total_out = sum(entry.debit for entry in cashbook_entries)
    net_flow = total_in - total_out

    # Prepare table data
    table_data = []
    for entry in cashbook_entries:
        table_data.append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'description': entry.particulars or '',
            'inflow': float(entry.credit),
            'outflow': float(entry.debit)
        })
    for item in income_entries:
        table_data.append({
            'date': item.income_date.strftime('%Y-%m-%d'),
            'description': f"{item.description} (Other Income)",
            'inflow': float(item.amount),
            'outflow': 0
        })

    # Chart data
    daily_data = {}
    for row in table_data:
        d = row['date']
        if d not in daily_data:
            daily_data[d] = {'in': 0, 'out': 0, 'net': 0}
        daily_data[d]['in'] += row['inflow']
        daily_data[d]['out'] += row['outflow']
        daily_data[d]['net'] = daily_data[d]['in'] - daily_data[d]['out']

    chart_dates = sorted(daily_data.keys())
    chart_inflows = [daily_data[d]['in'] for d in chart_dates]
    chart_outflows = [daily_data[d]['out'] for d in chart_dates]
    chart_net = [daily_data[d]['net'] for d in chart_dates]

    return jsonify({
        'total_in': total_in,
        'total_out': total_out,
        'net_flow': net_flow,
        'table_data': table_data,
        'chart_dates': chart_dates,
        'chart_inflows': chart_inflows,
        'chart_outflows': chart_outflows,
        'chart_net': chart_net
    })
