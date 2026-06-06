from flask import Blueprint
dashboard_bp = Blueprint("dashboard", __name__)
from routes.dashboard import *  # noqa
