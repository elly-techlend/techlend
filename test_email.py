from emails import send_reset_email
from flask import Flask

app = Flask(__name__)

@app.route("/test-email")
def test_email():
    result = send_reset_email("taremwaelly767@gmail.com", "TESTTOKEN123")
    return "Email sent!" if result else "Email failed."
