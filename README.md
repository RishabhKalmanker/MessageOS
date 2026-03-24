# MessageOS

Personal message intelligence system. Rules-based, zero AI at runtime, free to operate.

---

## Architecture

```
iPhone iOS Shortcuts → Render (FastAPI) → SQLite → Ntfy.sh → iPhone push
                                        → Obsidian vault (iCloud sync)
```

- **Backend:** Python + FastAPI on Render.com
- **Database:** SQLite (Phase 1), Supabase PostgreSQL (future)
- **Notifications:** ntfy.sh (urgent priority bypasses iOS Focus / DND)
- **Dashboard:** Obsidian vault synced to iPhone via iCloud

---

## Phase 1 Setup

### 1. Clone and install

```bash
git clone https://github.com/RishabhKalmanker/messageOS.git
cd messageOS
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create your .env

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `VIP_PHONES` — comma-separated phone numbers (e.g. `+14155551234,+16505559876`)
- `VIP_EMAILS` — comma-separated emails of VIP contacts
- `NTFY_TOKEN` — your ntfy.sh token (already filled in .env.example)

### 3. Run locally

```bash
uvicorn main:app --reload --port 8000
```

Test health: `curl http://localhost:8000/health`

### 4. Deploy to Render

1. Push to GitHub
2. Create a new **Web Service** on Render.com, connect the GitHub repo
3. Set **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add all environment variables from `.env` in the Render dashboard
5. Enable **Persistent Disk** (at least 1 GB) and mount at `/data`
   - Set `DATABASE_PATH=/data/messageos.db` in Render env vars
6. Deploy — note your Render URL (e.g. `https://messageos.onrender.com`)

---

## iOS Shortcuts Setup

Replace `YOUR_RENDER_URL` with your actual Render URL in each shortcut.

---

### Shortcut 1 — iMessage Received

**Trigger:** Automations → New Automation → Message → **Is Received** → From Anyone → Run Immediately (toggle off "Ask Before Running")

**Actions (in order):**

1. **Get Contents of URL**
   - URL: `https://YOUR_RENDER_URL/webhook/imessage-received`
   - Method: `POST`
   - Request Body: `JSON`
   - Add JSON fields:
     - Key: `sender` → Value: tap `+` → choose **Sender Name** (from Shortcuts variables)
     - Key: `phone` → Value: tap `+` → choose **Sender** (the phone number variable)
     - Key: `body` → Value: tap `+` → choose **Message Content**
     - Key: `timestamp` → Value: tap `+` → choose **Current Date** → format as **ISO 8601**

**Note:** In the Shortcut editor, when adding the JSON body, tap "Add new field" for each key-value pair. Use the variable picker (blue variable buttons) to select the iMessage-specific variables.

---

### Shortcut 2 — iMessage Sent

**Trigger:** Automations → New Automation → Message → **Is Sent** → Run Immediately (toggle off "Ask Before Running")

**Actions (in order):**

1. **Get Contents of URL**
   - URL: `https://YOUR_RENDER_URL/webhook/imessage-sent`
   - Method: `POST`
   - Request Body: `JSON`
   - Add JSON fields:
     - Key: `recipient` → Value: tap `+` → choose **Recipient** (the phone/contact variable)
     - Key: `timestamp` → Value: tap `+` → choose **Current Date** → format as **ISO 8601**

---

### Shortcut 3 — Call Ended Vibe Check

**Trigger:** Automations → New Automation → Phone → **Call Ends** → Run Immediately (toggle off "Ask Before Running")

**Actions (in order):**

1. **Choose from Menu**
   - Prompt: `How did it go?`
   - Options: `Great`, `Fine`, `Rough`
   - (This creates a variable — name it `Vibe`)

2. **Choose from Menu**
   - Prompt: `Follow up needed?`
   - Options: `Yes`, `No`
   - (Name this variable `Followup`)

3. **Ask for Input**
   - Prompt: `Any note?`
   - Input Type: Text
   - Allow empty input: YES
   - (Name this variable `CallNote`)

4. **Get Contents of URL**
   - URL: `https://YOUR_RENDER_URL/calls/log`
   - Method: `POST`
   - Request Body: `JSON`
   - Add JSON fields:
     - Key: `contact_name` → Value: tap `+` → choose **Last Called** (phone variable)
     - Key: `vibe` → Value: tap `+` → choose **Vibe** (result of step 1)
     - Key: `followup_needed` → Value: tap `+` → choose **Followup** (result of step 2)
     - Key: `note` → Value: tap `+` → choose **CallNote** (result of step 3)

---

### Shortcut 4 — Daily Contacts Sync

**Trigger:** Automations → New Automation → **Time of Day** → 3:00 AM → Daily → Run Immediately

**Actions (in order):**

1. **Find Contacts**
   - Filter: All contacts
   - (Keep all fields — name, phone numbers, email addresses)
   - Limit: OFF (sync all)

2. **Repeat with Each** (item in Contacts result)
   - Inside the repeat block, add a **Dictionary** action:
     - Key: `name` → Value: **Name** (contact variable)
     - Key: `phone` → Value: **Phone Numbers** (first phone number)
     - Key: `email` → Value: **Email Addresses** (first email address)
   - Add each dictionary to a **List** variable called `ContactList`

3. **Get Contents of URL**
   - URL: `https://YOUR_RENDER_URL/contacts/sync`
   - Method: `POST`
   - Request Body: `JSON`
   - Add JSON field:
     - Key: `contacts` → Value: **ContactList**

4. **Show Result** (optional — shows how many contacts were synced)

**Simpler alternative if the repeat approach is slow:**
Use **Get All Contacts** action and pass the result directly to the URL action — iOS Shortcuts will serialize the contacts array automatically.

---

### Shortcut 5 — Siri Tier Management

**Trigger:** Tap the `+` icon → Name the shortcut **"Manage MessageOS Contact"** → Add to Home Screen or use "Hey Siri, Manage MessageOS Contact"

**Actions (in order):**

1. **Ask for Input**
   - Prompt: `Contact name?`
   - Input Type: Text
   - (Name variable `ContactName`)

2. **Ask for Input**
   - Prompt: `New tier? Say VIP, Important, or Normal`
   - Input Type: Text
   - (Name variable `NewTier`)

3. **Get Contents of URL**
   - URL: `https://YOUR_RENDER_URL/siri/tier`
   - Method: `POST`
   - Request Body: `JSON`
   - Add JSON fields:
     - Key: `name` → Value: **ContactName**
     - Key: `tier` → Value: **NewTier**

4. **Speak Text**
   - Text: tap `+` → choose **Contents of URL** (result of step 3)

**Usage:** Say "Hey Siri, Manage MessageOS Contact" → answer the two prompts → Siri reads back the result.

---

## API Reference

### Phase 1 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/imessage-received` | Log incoming iMessage, start SLA clock if VIP/Important |
| POST | `/webhook/imessage-sent` | Log outbound iMessage, close SLA clocks |

### Request Payloads

**POST /webhook/imessage-received**
```json
{
  "sender": "John Smith",
  "phone": "+14155551234",
  "body": "Hey, quick question...",
  "timestamp": "2024-01-15T14:30:00Z"
}
```

**POST /webhook/imessage-sent**
```json
{
  "recipient": "+14155551234",
  "timestamp": "2024-01-15T14:45:00Z"
}
```

---

## SLA Thresholds

| Tier | Instant Alert | Escalation Warning | Breach |
|------|--------------|-------------------|--------|
| VIP | Yes (urgent) | 18 minutes | 20 minutes |
| Important | Yes (high) | 110 minutes | 120 minutes |
| Normal | No | No | No |

SLA time is automatically paused during busy calendar events (Phase 2).

---

## Environment Variables

See `.env.example` for the full list with descriptions.

**Required for Phase 1:**
- `NTFY_URL`
- `NTFY_TOKEN`
- `VIP_PHONES` and/or `VIP_EMAILS`
- `DATABASE_PATH`

---

## Project Structure

```
MessageOS/
├── main.py              # FastAPI app — all endpoints
├── database.py          # SQLite init and connection helper
├── contacts.py          # Contact lookup, creation, tier resolution
├── sla.py               # SLA clock management + watchdog thread
├── notifier.py          # Ntfy.sh push notification helper
├── requirements.txt
├── Procfile             # Render start command
├── .env.example         # Template for all env vars
├── .gitignore
└── README.md
```

Phase 2 adds: `gmail_poller.py`, `calendar_poller.py`, siri API endpoints
Phase 3 adds: `obsidian_writer.py`, relationship intelligence engine
