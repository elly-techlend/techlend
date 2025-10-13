from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from utils.logging import log_company_action, log_system_action
import os
from flask import send_file
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from forms import ChangePasswordForm
from extensions import csrf

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')

@settings_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ChangePasswordForm()
    
    if form.validate_on_submit():
        if not current_user.check_password(form.old_password.data):
            flash('Incorrect old password.', 'danger')
        elif form.new_password.data != form.confirm_password.data:
            flash('New passwords do not match.', 'danger')
        else:
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('settings.profile'))

    return render_template('settings/profile.html', form=form)

@csrf.exempt
@settings_bp.route('/system-preferences', methods=['GET', 'POST'])
@login_required
def system_preferences():
    if request.method == 'POST':
        theme = request.form.get('theme') or 'light'
        timezone = request.form.get('timezone') or ''
        language = request.form.get('language') or ''

        # Save preferences to the user model
        current_user.theme = theme
        current_user.timezone = timezone
        current_user.language = language
        db.session.commit()

        flash("Preferences saved successfully.", "success")

        # ✅ Redirect user to dashboard after saving
        return redirect(url_for('dashboard.index'))

    # Only render preferences page when accessed directly
    return render_template(
        'settings/system_preferences.html',
        theme=current_user.theme or 'light',
        timezone=current_user.timezone or '',
        language=current_user.language or ''
    )

@settings_bp.route('/security')
@login_required
def security_settings():
    if not current_user.is_superuser:
        flash("Access denied: Superuser access only.", "danger")
        return redirect(url_for('dashboard.index'))  # Or home/dashboard route

    return render_template('settings/security.html')

@settings_bp.route('/billing')
@login_required
def billing():
    return render_template('settings/billing.html')

@settings_bp.route('/notifications', methods=['GET', 'POST'])
@login_required
def notifications():
    if request.method == 'POST':
        # You can grab form data here and save to DB later
        email = 'on' if request.form.get('email_notifications') else 'off'
        sms = 'on' if request.form.get('sms_notifications') else 'off'
        system = 'on' if request.form.get('system_alerts') else 'off'

        # For now, just flash results (simulate saving)
        flash(f"Preferences updated: Email: {email}, SMS: {sms}, Alerts: {system}", 'success')

    return render_template('settings/notifications.html')

@settings_bp.route('/data', methods=['GET'])
@login_required
def data_management():
    return render_template('settings/data_management.html')

@settings_bp.route('/backup-database')
@login_required
def backup_database():
    if not current_user.is_superuser:
        flash("Only superusers can back up the database.", "danger")
        return redirect(url_for('settings.data_management'))

    # Simulate backup file path
    backup_file = os.path.join('backups', 'db_backup.sql')  # Assume it exists
    if os.path.exists(backup_file):
        return send_file(backup_file, as_attachment=True)
    else:
        flash("Backup file not found.", "danger")
        return redirect(url_for('settings.data_management'))

@settings_bp.route('/restore-database', methods=['POST'])
@login_required
def restore_database():
    if not current_user.is_superuser:
        flash("Only superusers can restore the database.", "danger")
        return redirect(url_for('settings.data_management'))

    file = request.files.get('backup_file')
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join('uploads', filename)
        file.save(filepath)
        flash(f"Uploaded file {filename}. Restore logic not implemented yet.", "info")
    else:
        flash("No file uploaded.", "warning")

    return redirect(url_for('settings.data_management'))

@settings_bp.route('/delete-all-data', methods=['POST'])
@login_required
def delete_all_data():
    if not current_user.is_superuser:
        flash("Only superusers can delete system data.", "danger")
        return redirect(url_for('settings.data_management'))

    # NOTE: You should never actually delete data like this without confirmation + real logic
    flash("Simulated deletion of all data. No real data was harmed.", "warning")
    return redirect(url_for('settings.data_management'))

@settings_bp.route('/integrations')
@login_required
def integrations():
    if not current_user.is_superuser:
        flash("Only superusers can access integrations.", "danger")
        return redirect(url_for('dashboard.index'))
    return render_template('settings/integrations.html')

@settings_bp.route('/labs')
@login_required
def labs():
    if not current_user.is_superuser:
        flash("Only superusers can access Labs.", "danger")
        return redirect(url_for('dashboard'))
    return render_template('settings/labs.html')

@settings_bp.route('/submit-feature-request', methods=['POST'])
@login_required
def submit_feature_request():
    if not current_user.is_superuser:
        flash("Only superusers can submit feature requests.", "danger")
        return redirect(url_for('settings.labs'))

    feature_request = request.form.get('feature_request')
    # In a real scenario, you'd save this to the database or send an email.
    flash(f"Feature request submitted: {feature_request}", "info")
    return redirect(url_for('settings.labs'))

@settings_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    # Your logic for password change
    return render_template('settings/change_password.html')

@settings_bp.route('/superuser/logs')
@superuser_required
def view_system_logs():
    logs = SystemLog.query.order_by(SystemLog.created_at.desc()).all()
    return render_template('superuser/system_logs.html', logs=logs)

@settings_bp.route('/superuser/logs/clear', methods=['POST'])
@superuser_required
def clear_system_logs():
    from models import SystemLog
    SystemLog.query.delete()
    db.session.commit()
    flash("All system logs cleared.", "success")
    return redirect(url_for('settings.view_system_logs'))
