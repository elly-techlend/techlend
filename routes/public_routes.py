# routes/public_routes.py
from flask import Blueprint, render_template

# Create a Blueprint for the public (landing page)
public_bp = Blueprint('public', __name__)

# Define the route for landing page
@public_bp.route('/landing')
def index():
    return render_template('landing.html')  # The template for your landing page
