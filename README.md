# Fieldwork

A local-first job-search and application-tracking app. Runs on your Mac at **http://127.0.0.1:8765**. Discovery comes from actual job sources; application history stays in a local SQLite database.

## Open the app

Double-click **Start Fieldwork.command** in this folder. A Terminal window runs the app and your browser opens. Leave that Terminal window running. Press Control-C there to stop it. Your records survive restarts.

The launcher creates a local virtual environment and installs `requirements.txt` on first launch. Python 3.10 or later is required.

There is no startup service or background search while the application is closed. Searches are launched manually. Folder updates are checked every 15 seconds while the app is running.

## Configure your profile

- CTO, CPTO, VP Engineering, VP Technology, CIO and comparable technology leadership.
- Product/engineering and internal technology leadership both included.
- Remote preferred; nearby roles within approximately 25 miles of Boston, MA (example location; change before searching).
- Smaller organizations and non-medical nonprofits preferred, full-time preferred, no compensation floor.
- Cryptocurrency, gambling, financial services, healthcare, biotech, pharmaceuticals, medical devices and health-focused nonprofits excluded.

Search criteria edits are saved locally. List fields take one item per line. The advanced editor exposes all settings, including integration paths, source page limits and verification limits. **Rerank saved roles** applies updated criteria to existing records.

The repository includes an example search profile, not personal background data. Set your name, experience, location, target roles, and preferences in Search criteria before searching.

## Finding and reviewing jobs

1. Click **Run search**. An optional focus narrows LinkedIn queries and AI deep search; watchlist and feed scans continue to cover the full profile.
2. Inspect the activity log. Each source reports scanned/matched counts or its error. Runs remain available after completion.
3. Review the top results, their source links, and their verification labels.
4. Use **Details & notes → Review employer and location facts** to correct industry, size, location and work arrangement. Those corrections are protected from subsequent source refreshes.
5. Mark promising roles **Interested**, then record applications and later stages.

A search may take several minutes. Stop cancels the active run; listings already saved remain. Repeated source searches use a cache to avoid excessive requests.

### What the labels mean

- **Rules**: a transparent preliminary ranking, not a model judgment or hiring probability. Missing employer industry or work arrangement caps this ranking at 64 and adds a review flag.
- **AI fit**: optional model-based relevance scoring using your profile and posting text.
- **Verified open**: the page provided matching posting and application evidence when checked. It is not a guarantee that an employer is actively recruiting.
- **Unverified**: not checked, access blocked, rate-limited, or inconclusive. These records are retained.
- **Posting closed**: HTTP 404/410 or an explicit closed notice. This never changes your application status.

Appearing in a remote search does **not** establish remote eligibility. Unknown arrangements stay unknown. Nearby town names are an approximate shortlist; postings with coordinates use straight-line distance. Confirm actual commute and Massachusetts eligibility with the employer.

Industry filtering uses the employer name, role title and available employer industry metadata—not industries mentioned as customers in a description. An unfamiliar employer can remain unclassified. The review flag is important: keyword filtering alone cannot identify every excluded business.

### Sources

Enabled initially: LinkedIn public search, a starter nonprofit/technology watchlist, Remotive, RemoteOK, and The Muse. The watchlist supports Greenhouse, Lever and Ashby. The supplied organizations are starting points, not an exhaustive employer universe.

LinkedIn is an unofficial public integration and may change or throttle access. The app spaces requests and pauses that host after rate limiting. Public feeds are cached for six hours, ATS boards for 15 minutes. Default page depth is three per query; description enrichment and availability checking are capped at the top 40 postings per run. Increase these limits in the advanced profile if needed, respecting source restrictions.

Built In is an experimental structured-page adapter, disabled by default. Adzuna is implemented but disabled until you supply credentials and enable it. Job discovery does not use a job-board login, CAPTCHA bypass, or paid subscription. Microsoft 365 reconciliation is an optional, separately authorized read-only connection.

Source references:
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever postings API](https://github.com/lever/postings-api)
- [Ashby public postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [Remotive API and usage terms](https://github.com/remotive-io/remote-jobs-api)
- [Adzuna search API](https://developer.adzuna.com/docs/search)

## Optional AI

Choose **Anthropic or OpenAI independently for scoring, email classification and deep search**. You can mix providers or disable AI for individual tasks.

In **Search criteria → AI settings · Anthropic & OpenAI**:

1. Enter one or both provider API keys.
2. Click **Save keys & refresh available models**.
3. Select each task's model from the dropdown. Models are grouped by provider and loaded from the connected accounts, including all pages of Anthropic's catalog.
4. Click **Save criteria**, then rerank saved roles or run a new search.

Keys are stored separately in a local `.env` file with owner-only permissions and never returned to the browser. Leave a key field blank to retain its saved value. OpenAI uses `OPENAI_API_KEY`; Anthropic uses `ANTHROPIC_API_KEY`. Existing Anthropic choices are preserved when upgrading.

Model catalogs indicate account availability, not guaranteed compatibility with every feature. Known image/audio/embedding model families are excluded from these text-task selectors. Unsupported structured-tool or web-search requests surface the provider's error and never silently switch to a different model. Failure of one provider's catalog does not hide the other provider's models.

The selected provider receives the professional profile and job descriptions for scoring. Email contents are sent only when you explicitly classify pasted text or uploaded files. No inbox is connected. Paid requests use your API account; no cost estimate is presented as a guarantee.

Both integrations validate forced structured tool output against the same schema. OpenAI uses the Responses API with response storage disabled. Deep search accepts only URLs observed in search sources/citations and then independently checks those pages. Anthropic search continuations are bounded; OpenAI search has a tool-call cap.

Optional Adzuna credentials are configured by copying `.env.example` to `.env` and filling `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`; restart after editing that file.

Provider routing, model pagination, error isolation, credential handling and structured output are tested with mocked API responses. A paid end-to-end generation has not been performed without user-provided API credentials.

API references: [OpenAI models](https://developers.openai.com/api/reference/resources/models/methods/list), [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search), [Anthropic models](https://platform.claude.com/docs/en/api/models/list).

## Tracking and updates

Status history records the evidence date, import date and provenance. Automated updates cannot downgrade stages, replace protected terminal outcomes or overwrite explicitly locked user corrections. **Explicit correction** in a role's detail view overrides the guard and protects the resulting status; unlock it there to allow future automatic updates.

A closed posting and a closed application process are separate. `Lost` can be revived by later evidence. The silence rule applies to applied/acknowledged records, starting from the application date and resetting on later stage evidence; routine notes and fetch timestamps do not reset it. Screening and interviewing are never silently marked lost.

Drag `.eml`, `.msg` or `.txt` files into **Log a response**, or paste text. AI classification proposes an employer, stage and summary; you choose the correct record and apply the reviewed result. You can always record a response manually without AI. Reimporting an identical classified message does not duplicate its event.

### Drafting handoff

**Copy brief** copies a prompt containing the professional summary and specific posting details. No letter is submitted automatically, and drafting a letter does not mark a role applied.

Set `cover_letter_dir` in the advanced profile to enable letter links. Filename matching only links a file when it identifies one unambiguous listing. `secondary_updates_dir` enables a second drop folder for another assistant's workspace.

Drop update files into `data/updates/`:

```json
{
  "company": "Example Organization",
  "title": "Chief Technology Officer",
  "status": "applied",
  "note": "Submitted through the employer site",
  "applied_date": "2026-09-22",
  "create_if_missing": true,
  "force": false,
  "cover_letter": "Example-Organization-letter.docx"
}
```

`status`, `title`, `note`, `applied_date`, `cover_letter`, `create_if_missing` and `force` are optional. Company is required. `occurred_at` may supply an ISO evidence timestamp. A missing title creates a placeholder only when explicitly requested and no ambiguous company record exists. Later discovery can fold a unique placeholder into a real posting.

Successful/invalid files are archived with result prefixes. Unmatched or ambiguous updates remain pending instead of being discarded; see **Update inbox**. Repeated identical update contents are idempotent.

Cross-board deduplication requires matching normalized company, title and location. Distinct same-source requisitions are retained. If a merge was incorrect, use **Separate this posting** in the source-record section of role details. Existing application history stays with the original record and both records remain separate on future imports.

## Reports and data

The report supports sortable columns, column visibility, status filters, idle days and funnel counts segmented by role family, size and original discovery source. Company size remains unknown when unsupported. Conversion metrics use stage history; rejections do not change the profile or train a preference model.

**Export your data** downloads a JSON snapshot of listings, events, source records and profile. For a complete restorable filesystem backup, stop the application and copy the entire `data/` folder. There is no in-app restore wizard.

Data locations:
- `data/jobs.sqlite3`: listings, source records, events, runs and logs
- `data/profile.json`: targeting profile
- `data/settings.json`: model choices (no keys)
- `data/updates/` and `data/archive/`: file integration inbox and processed files
- `.env`: optional credentials; back this up separately and privately

## Validation

The automated suite covers matching and exclusions, status ordering and explicit corrections, duplicate imports, ambiguous matches, placeholder folding, merge separation, verification failures, protected user facts, schema failures, local request protections and database descriptor stability over 1,000 API requests.

Run from this directory using the installed workspace environment:

```sh
../../.venv/bin/python -m pytest tests -q
```

Or use `.venv/bin/python` if the launcher installed a standalone environment here.

The application was also checked in the browser for search, filtering, criteria editing, reports, posting details and clipboard drafting briefs. The initial database contains real discovery results, not sample jobs. Discovery coverage is bounded by enabled sources and page limits; it is not an exhaustive search of every employer.

## Microsoft 365 inbox reconciliation

Open **Microsoft 365 reconciliation** in the sidebar. The first scan covers the past **six calendar months**, including Inbox and every nested subfolder (with pagination). Folders outside Inbox, shared mailboxes, and Online Archive are not included. The scan reads mail locally and saves likely application messages in a durable review queue. A local phrase-based filter separates application correspondence, uncertain hiring messages, and alerts/other mail. Employer names and generic career keywords alone do not qualify as application evidence. The filter may still include false positives or miss unusually worded messages; this is not a guarantee that every application is found. Repeated scans deduplicate messages, including moves between folders.

### One-time registration (tenant administrator)

1. Open [Microsoft Entra App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade). Register **Fieldwork Local**, supporting accounts in this organizational directory only.
2. In **Authentication → Add a platform → Mobile and desktop applications**, add the custom redirect URI `http://localhost:8765/api/mailbox/callback`.
3. Under **API permissions**, add Microsoft Graph **Delegated permissions** `Mail.Read` and `User.Read`. Do not add application permissions, `Mail.ReadWrite`, or `Mail.Send`. No client secret is used. If your tenant requires administrator consent, grant it for these permissions.
4. Copy **Directory (tenant) ID** and **Application (client) ID** from Overview into Fieldwork's connection setup.
5. Click **Save settings & prepare Microsoft sign-in**, then follow the Microsoft sign-in link and consent. Return to Fieldwork and click **Refresh review**, then **Scan past six months**.

Authorization uses PKCE and a one-time, expiring state value. Microsoft permission applies to your signed-in mailbox; the scanner limits its requests to Inbox and descendants. `offline_access` allows token renewal during this running app session. Access and refresh tokens stay in memory, never in the database or browser responses. Restarting Fieldwork requires reconnecting. Disconnect clears tokens and stops any scan; previously collected local evidence remains. Revoke the application's consent in Microsoft if you want to revoke its grant entirely.

### Review and apply

- Read each candidate locally, then manually select a saved opportunity and its status, or check messages and click **Interpret checked messages with AI**. This explicitly sends their subject, sender, received date and message text (up to 40,000 characters, including quoted text) to the selected email-classification provider. No attachments are fetched or sent.
- AI suggests company, role, and status. Matching requires a unique company/role match; you resolve ambiguous matches. Job alerts and unrelated mail can be dismissed without modifying the actual mailbox.
- Click **Apply reviewed status** to record original evidence dates and a deduplication fingerprint. Historical evidence can update newly discovered/shortlisted jobs. Newer, terminal, and manually corrected statuses remain protected unless you explicitly check the override box. The returned message says when evidence was retained without changing status.
- You can create a missing opportunity from reviewed company/title fields. No tracker updates happen merely from scanning or interpretation.
- Scan failures leave completed evidence intact; scan again to retry. Refresh review to see messages collected since the dialog opened. AI batch failures preserve completed interpretations; retry uses cached results.

This integration has mocked regression coverage for OAuth, pagination, nested folders, deduplication, review, historical status guards, and API protection. A live tenant sign-in and real mailbox scan still require your app registration and consent.

References: [Microsoft authorization code flow with PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow), [mail folders](https://learn.microsoft.com/en-us/graph/api/resources/mailfolder?view=graph-rest-1.0), [delegated Mail.Read](https://learn.microsoft.com/en-us/graph/permissions-reference#mailread).

## Development

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest tests -q
python app.py
```

## Local data

Keep `.env` and `data/` private. They contain provider credentials, personal profile settings, job records, and optionally mailbox evidence. These paths, virtual environments, and caches are excluded from Git. This app binds to localhost and is designed for one user; do not expose it directly as a public web service. Publishing the source does not publish your running app or its data.
