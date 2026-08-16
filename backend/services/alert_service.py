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


def send_question_email(question: str, options: list = None, job_title: str = "",
                        company: str = "", url: str = "", hint: str = "") -> bool:
    """
    Ask you a screening question the profile and resume could not answer.

    One email per question, because the reply has to be attributable to exactly
    one question: the token in the subject is what services.inbox_service reads
    to file your answer against the right one. Batching several into one email
    would leave a one-line reply ambiguous.

    Answer either by replying to this email (if IMAP polling is configured) or
    from Setup > Saved answers. Either way the answer is learned and reused, so
    each question is only ever asked once.
    """
    from services.answer_service import question_token

    token = question_token(question)
    app_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    opts_html = ""
    if options:
        opts_html = ("<p style='color:#9E9E9E;margin:16px 0 4px;'>Allowed answers — "
                     "reply with exactly one:</p><ul style='color:#E0E0E0;'>"
                     + "".join(f"<li>{o}</li>" for o in options) + "</ul>")

    context = " @ ".join(x for x in (job_title, company) if x)
    # What the form said when it rejected our answer — "Enter a decimal number
    # larger than 0.0" tells you to reply "0", not "Immediately". Shown here
    # rather than folded into the question, which is the key the answer is
    # stored and reused under.
    hint_html = (f"<p style='color:#9E9E9E;margin:12px 0 0;'>The form rejected our answer with: "
                 f"<em style='color:#E0E0E0;'>{hint}</em></p>") if hint else ""
    html = f"""
    <div style="font-family: Inter, sans-serif; background: #0F1117; color: #E0E0E0; padding: 24px; border-radius: 8px;">
        <h2 style="color: #4FC3F7;">A question I can't answer from your resume</h2>
        {f'<p style="color:#9E9E9E;">Came up on: {context}</p>' if context else ''}
        <p style="font-size:17px;margin:20px 0;"><strong>{question}</strong></p>
        {hint_html}
        {opts_html}
        <p style="color:#9E9E9E;margin-top:20px;">
          <strong style="color:#E0E0E0;">Reply to this email</strong> with just the answer on
          the first line. Keep the subject line as-is — the code in it is how I know
          which question you're answering.
        </p>
        <p style="color:#9E9E9E;">Or answer it in the app:
          <a href="{app_url}/setup" style="color:#4FC3F7;">Setup &rsaquo; Saved answers</a></p>
        {f'<p style="color:#9E9E9E;">Posting: <a href="{url}" style="color:#4FC3F7;">{url}</a></p>' if url else ''}
        <p style="color:#5A5F73;font-size:12px;margin-top:24px;">
          I won't apply to this one until it's answered, and I'll never guess. Once you
          answer, it's reused for every posting that asks the same thing.</p>
    </div>
    """
    message = Mail(
        from_email=os.environ.get("SENDGRID_FROM_EMAIL"),
        to_emails=os.environ.get("MY_EMAIL"),
        subject=f"[JHQ:{token}] {question[:110]}",
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))
        sg.send(message)
        print(f"    Question emailed [{token}]: {question[:60]}")
        return True
    except Exception as e:
        print(f"    Question email failed: {e}")
        return False


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
