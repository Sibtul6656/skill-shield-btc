# Naye Users ka Data — Google Sheet (Google Drive) mein Collect Karna

Ye sabse simple aur reliable tareeqa hai bina kisi paid service ya complex
Google Cloud service-account setup ke. Har naya signup automatically Google
Sheet mein ek row ban jayega — aur Sheet khud Google Drive mein hi save hoti
hai, to effectively ye "Drive mein data collect karna" hi hai.

## Step 1 — Google Sheet banayein

1. https://sheets.google.com par jayein, naya blank sheet banayein.
2. Naam de dein: `Skill Shield BTC - New Signups`
3. Pehli row mein headers likh dein: `email | signup_time_utc | referred_by | plan_status`

## Step 2 — Apps Script attach karein

1. Sheet ke andar: **Extensions → Apps Script**
2. Jo default code hai usay delete karke ye paste kar dein:

```javascript
function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var data = JSON.parse(e.postData.contents);

  sheet.appendRow([
    data.email || "",
    data.signup_time_utc || "",
    data.referred_by || "",
    data.plan_status || ""
  ]);

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

3. **Save** karein (disk icon), project ko koi bhi naam de dein (e.g. `SignupWebhook`).

## Step 3 — Web App ke tor pe Deploy karein

1. Upar right mein **Deploy → New deployment**
2. Gear icon (⚙️) pe click karke type select karein: **Web app**
3. Settings:
   - **Execute as:** Me (aapka Google account)
   - **Who has access:** Anyone
4. **Deploy** pe click karein → Google aapse permissions maangega, allow kar dein.
5. Aapko ek URL milega jaisa: `https://script.google.com/macros/s/AKfycb.../exec`
   — ye URL copy kar lein.

## Step 4 — Server par env variable set karein

Jahan bhi ye app host hai (Replit / server), wahan ye environment variable
add kar dein:

```
SIGNUP_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/AKfycb.../exec
```

Bas — is variable ke set hote hi, har naya signup automatically is Google
Sheet mein ek naya row bana dega. Koi code change dobara nahi karni.

## Admin ko personal email par notification (contact form + new signup)

Isi tarah, admin ki personal email par notification bhejne ke liye ye
environment variables set karein:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-gmail-address@gmail.com
SMTP_PASSWORD=your-gmail-app-password      # normal password nahi, "App Password" use karein
SMTP_FROM=support@skillshieldbtc.com
ADMIN_NOTIFY_EMAIL=client-personal-email@gmail.com
```

Gmail App Password banane ka tareeqa: Google Account → Security → 2-Step
Verification (on karein) → App Passwords → naya password generate karein →
wahi `SMTP_PASSWORD` mein paste karein. (Normal Gmail password kaam nahi
karega, Google isay block kar deta hai.)

Agar Gmail ke ilawa koi aur email provider use karna hai (Outlook, Zoho,
custom domain email, SendGrid, etc.) — unke SMTP host/port bhi issi tarah
kaam karenge, bas values badalni hongi.

## Ye sab optional/safe hai

Agar `SIGNUP_SHEETS_WEBHOOK_URL` ya SMTP variables set nahi kiye jate, to
app bilkul normal chalta rahega — sirf ye extra notifications silently
disabled reh jayengi. Koi cheez break nahi hogi, koi error nahi aayega.
