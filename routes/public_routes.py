# routes/public_routes.py
from flask_login import current_user
from flask import render_template
from flask import Blueprint, redirect, url_for
from datetime import datetime

public_bp = Blueprint('public', __name__)

# Define the route for landing page
@public_bp.route('/')
def root():
    if current_user.is_authenticated:
        # Redirect logged-in users to dashboard
        return redirect(url_for('index'))
    else:
        # Public landing page for everyone else
        return redirect(url_for('public.landing'))

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
def home_redirect():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    else:
        return redirect(url_for('public.landing'))
