import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

load_dotenv()


def send_email_alert(job: dict):
    score = job.get("match_score", 0)
    title = job.get("title", "")
    company = job.get("company", "")
    url = job.get("url", "")
    gaps = job.get("gap_analysis", [])[:3]
    breakdown = job.get("score_breakdown", {})

    skills_score = breakdown.get("skills_score", 0)
    exp_score = breakdown.get("experience_score", 0)
    domain_score = breakdown.get("domain_score", 0)

    gaps_html = "".join(f"<li>{g}</li>" for g in gaps) if gaps else "<li>None identified</li>"

    html = f"""
    <div style="font-family: Inter, sans-serif; background: #0F1117; color: #E0E0E0; padding: 24px; border-radius: 8px;">
        <h2 style="color: #4FC3F7;">🔥 {score}% Match: {title} @ {company}</h2>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr>
                <td style="padding: 8px; color: #9E9E9E;">Skills</td>
                <td style="padding: 8px; color: #4CAF50;">{skills_score}/40</td>
            </tr>
            <tr>
                <td style="padding: 8px; color: #9E9E9E;">Experience</td>
                <td style="padding: 8px; color: #4CAF50;">{exp_score}/30</td>
            </tr>
            <tr>
                <td style="padding: 8px; color: #9E9E9E;">Domain</td>
                <td style="padding: 8px; color: #4CAF50;">{domain_score}/20</td>
            </tr>
        </table>
        <h3 style="color: #FFC107;">Skill Gaps</h3>
        <ul style="color: #FF5370;">{gaps_html}</ul>
        <div style="margin-top: 24px;">
            <a href="{url}" style="background: #4FC3F7; color: #0F1117; padding: 12px 24px; border-radius: 4px; text-decoration: none; margin-right: 12px;">Apply Now</a>
            <a href="{os.environ.get('FRONTEND_URL', 'http://localhost:3000')}/review" style="background: #1A1D2E; color: #4FC3F7; padding: 12px 24px; border-radius: 4px; text-decoration: none; border: 1px solid #4FC3F7;">Open Review</a>
        </div>
    </div>
    """

    message = Mail(
        from_email=os.environ.get("SENDGRID_FROM_EMAIL"),
        to_emails=os.environ.get("MY_EMAIL"),
        subject=f"🔥 {score}% Match: {title} @ {company}",
        html_content=html,
    )

    try:
        sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))
        sg.send(message)
        print(f"Email alert sent for {title} @ {company}")
    except Exception as e:
        print(f"Email alert failed: {e}")


def send_sms_alert(job: dict):
    score = job.get("match_score", 0)
    title = job.get("title", "")
    company = job.get("company", "")
    url = job.get("url", "")

    body = f"Job Alert: {score}% match: {title} @ {company}. Apply: {url}"
    if len(body) > 160:
        body = body[:157] + "..."

    try:
        client = TwilioClient(
            os.environ.get("TWILIO_ACCOUNT_SID"),
            os.environ.get("TWILIO_AUTH_TOKEN"),
        )
        client.messages.create(
            body=body,
            from_=os.environ.get("TWILIO_PHONE_NUMBER"),
            to=os.environ.get("MY_PHONE"),
        )
        print(f"SMS alert sent for {title} @ {company}")
    except Exception as e:
        print(f"SMS alert failed: {e}")


def send_manual_action_alert(platform: str, reason: str, url: str = ""):
    """
    Fired when the headed apply browser hits a CAPTCHA / 2FA / verification step
    that only a human can clear. The browser window stays open and waits — this
    just pings you to go solve it.
    """
    html = f"""
    <div style="font-family: Inter, sans-serif; background: #0F1117; color: #E0E0E0; padding: 24px; border-radius: 8px;">
        <h2 style="color: #FFC107;">🙋 Manual action needed — {platform.title()}</h2>
        <p><strong style="color: #4FC3F7;">{reason}</strong></p>
        <p style="color: #9E9E9E;">The auto-apply browser window is open and paused, waiting for you to
        solve it. Once you do, it continues on its own. If you don't act in time, this apply is skipped.</p>
        {f'<p style="color:#9E9E9E;">Page: <a href="{url}" style="color:#4FC3F7;">{url}</a></p>' if url else ''}
    </div>
    """
    message = Mail(
        from_email=os.environ.get("SENDGRID_FROM_EMAIL"),
        to_emails=os.environ.get("MY_EMAIL"),
        subject=f"🙋 JobHunt Copilot: solve {platform.title()} verification to continue applying",
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))
        sg.send(message)
        print(f"Manual-action email sent for {platform}: {reason}")
    except Exception as e:
        print(f"Manual-action email failed: {e}")

    body = f"JobHunt: solve {platform.title()} verification in the open browser to keep applying."
    try:
        client = TwilioClient(
            os.environ.get("TWILIO_ACCOUNT_SID"),
            os.environ.get("TWILIO_AUTH_TOKEN"),
        )
        client.messages.create(
            body=body[:160],
            from_=os.environ.get("TWILIO_PHONE_NUMBER"),
            to=os.environ.get("MY_PHONE"),
        )
    except Exception as e:
        print(f"Manual-action SMS failed: {e}")
