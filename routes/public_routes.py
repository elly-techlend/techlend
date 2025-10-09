from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user, login_required
from datetime import datetime

public_bp = Blueprint('public', __name__)

@public_bp.route('/')
def root():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    return render_template('landing.html')

@public_bp.route("/landing")
def landing():
    return render_template("landing.html")

@public_bp.route("/terms")
def terms():
    return render_template("terms.html", last_update=datetime.utcnow())

@public_bp.route("/privacy")
def privacy():
    return render_template("privacy.html", last_update=datetime.utcnow())

@public_bp.route("/home")
@login_required
def home_redirect():
    return redirect(url_for('dashboard.index'))
