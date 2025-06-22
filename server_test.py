import os
import time
import json
import email
import sqlite3
import threading
from email.message import EmailMessage
from aiosmtpd.controller import Controller
from flask import Flask, render_template_string, request, redirect, url_for
from flask_httpauth import HTTPBasicAuth
import smtplib

# Constants and directories
DB_FILE = "emails.db"
ATTACHMENTS_DIR = "attachments"

if not os.path.exists(ATTACHMENTS_DIR):
    os.makedirs(ATTACHMENTS_DIR)

#########################################
# Database functions to persist emails. #
#########################################

def init_db():
    """Initialize the SQLite database and create table if needed."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            body TEXT,
            date TEXT,
            attachments TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_email_to_db(sender, recipients, subject, body, date_str, attachments):
    """Save the email data to the database."""
    attachments_json = json.dumps(attachments)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO emails (sender, recipients, subject, body, date, attachments)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (sender, recipients, subject, body, date_str, attachments_json))
    conn.commit()
    conn.close()

init_db()

#########################################
# SMTP Server Handler                   #
#########################################

class EmailHandler:
    async def handle_DATA(self, server, session, envelope):
        """
        This method is called whenever an email is received.
        It parses the email to extract key details and saves it to the database.
        """
        try:
            msg = email.message_from_bytes(envelope.content)
        except Exception as e:
            print("Error parsing message:", e)
            return "550 Error"
        
        # Extract header details.
        sender = msg.get("From", envelope.mail_from)
        recipients = msg.get("To", ", ".join(envelope.rcpt_tos))
        subject = msg.get("Subject", "(No Subject)")
        date_str = msg.get("Date", time.strftime("%Y-%m-%d %H:%M:%S"))

        body = ""
        attachments = []

        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = part.get("Content-Disposition", "")
                # Skip container multipart parts.
                if part.get_content_maintype() == "multipart":
                    continue
                # Save attachments.
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        filepath = os.path.join(ATTACHMENTS_DIR, filename)
                        with open(filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        attachments.append(filepath)
                else:
                    # Otherwise, extract the textual content.
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            body += payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                        except Exception as e:
                            body += str(payload)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                try:
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                except Exception as e:
                    body = str(payload)

        # Persist the email in the database.
        save_email_to_db(sender, recipients, subject, body, date_str, attachments)
        print(f"Saved email from {sender} with subject '{subject}'")
        return "250 Message accepted for delivery"

def run_smtp_server():
    """Start the SMTP server (using aiosmtpd) in the background."""
    controller = Controller(EmailHandler(), hostname="localhost", port=1025)
    controller.start()
    print("SMTP server running on localhost:1025")
    return controller

#########################################
# Flask Web App with Basic Auth         #
#########################################

app = Flask(__name__)
auth = HTTPBasicAuth()

# Define simple user credentials.
users = {
    "admin": "secret"
}
@auth.get_password
def get_pw(username):
    if username in users:
        return users.get(username)
    return None

# HTML template for the inbox and send email form.
TEMPLATE = """
<!doctype html>
<html>
  <head>
    <title>Local Mail Server Inbox</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 40px; }
      h1 { color: #333; }
      ul { list-style-type: none; padding: 0; }
      li { background: #f5f5f5; margin: 10px 0; padding: 10px; border-radius: 5px; }
      pre { white-space: pre-wrap; word-break: break-all; }
      form { margin-top: 30px; }
      label { display: block; margin-top: 10px; }
    </style>
  </head>
  <body>
    <h1>Inbox</h1>
    {% if emails %}
      <ul>
      {% for em in emails %}
        <li>
          <p><strong>From:</strong> {{ em['sender'] }}</p>
          <p><strong>To:</strong> {{ em['recipients'] }}</p>
          <p><strong>Date:</strong> {{ em['date'] }}</p>
          <p><strong>Subject:</strong> {{ em['subject'] }}</p>
          <pre>{{ em['body'] }}</pre>
          {% if em['attachments'] %}
            <p><strong>Attachments:</strong></p>
            <ul>
              {% for att in em['attachments'] %}
                <li>{{ att }}</li>
              {% endfor %}
            </ul>
          {% endif %}
        </li>
      {% endfor %}
      </ul>
    {% else %}
      <p>No emails found.</p>
    {% endif %}
    <hr>
    <h2>Send Email</h2>
    <form method="post" action="/send">
      <label>From:
        <input type="text" name="from" value="test@example.com" required>
      </label>
      <label>To:
        <input type="text" name="to" placeholder="recipient@example.com" required>
      </label>
      <label>Subject:
        <input type="text" name="subject" required>
      </label>
      <label>Message:
        <textarea name="message" rows="6" cols="50" required></textarea>
      </label>
      <button type="submit">Send Email</button>
    </form>
  </body>
</html>
"""

@app.route("/")
@auth.login_required
def inbox():
    """Render the inbox by querying emails from the database."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender, recipients, subject, body, date, attachments FROM emails ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    
    emails = []
    for row in rows:
        sender, recipients, subject, body, date_str, attachments_json = row
        attachments = json.loads(attachments_json) if attachments_json else []
        emails.append({
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "body": body,
            "date": date_str,
            "attachments": attachments,
        })
        
    return render_template_string(TEMPLATE, emails=emails)

@app.route("/send", methods=["POST"])
@auth.login_required
def send_email():
    """
    Send email via a form submit by connecting to the local SMTP server.
    This is primarily for testing the sending side of your application.
    """
    email_from = request.form.get("from")
    email_to = request.form.get("to")
    subject = request.form.get("subject")
    message_body = request.form.get("message")

    msg = EmailMessage()
    msg.set_content(message_body)
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    try:
        with smtplib.SMTP("localhost", 1025) as smtp:
            smtp.send_message(msg)
        print(f"Sent email from {email_from} to {email_to}")
    except Exception as e:
        print("Error sending email:", e)
    return redirect(url_for("inbox"))

def run_flask_app():
    print("Flask web server running on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)

#########################################
# Entry Point                           #
#########################################

if __name__ == "__main__":
    # Start the SMTP server (background thread).
    smtp_controller = run_smtp_server()
    try:
        run_flask_app()
    finally:
        smtp_controller.stop()
        print("SMTP server stopped.")
