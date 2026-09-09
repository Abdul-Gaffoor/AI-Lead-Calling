# Swaraj Solar AI Sales Automation Platform — Final MVP

**Primary objective:** automate daily solar lead calling, qualification, follow-up and sales handoff in **Telugu + English**, reducing the amount of manual calling performed by sales executives.

```
                SWARAJ SOLAR WEBSITE
                        +
               DAILY EXCEL/CSV LEADS
                        │
                        ▼
                LEAD MANAGEMENT
                        │
              Validation / Consent
              Duplicate / DNC Check
                        │
                        ▼
                 CAMPAIGN ENGINE
                        │
                        ▼
              CLOUD TELEPHONY
              Virtual Business Number
                        │
                        ▼
                    CUSTOMER
                        │
                 Telugu / English
                        │
                        ▼
                 AI VOICE AGENT
           ┌────────────┼────────────┐
           │            │            │
          STT          LLM          TTS
           │            │            │
           └────────────┼────────────┘
                        │
                        ▼
                INTENT / SERVICE
                    CLASSIFIER
                        │
       ┌────────────────┼─────────────────┐
       ▼                ▼                 ▼
 Residential       Commercial         Service
 PM Surya Ghar     Industrial         Cleaning
 Agriculture       Ground Mount       Existing Solar
       └────────────────┼─────────────────┘
                        ▼
              QUALIFICATION ENGINE
                        │
                        ▼
               SOLAR / ROI ENGINE
                        │
                        ▼
                 LEAD SCORING
                        │
            ┌───────────┼────────────┐
            ▼           ▼            ▼
           HOT         WARM         COLD
            │           │
            ▼           ▼
       Sales Team    Follow-up
            │
            ▼
       Site Survey
            │
            ▼
        Quotation
            │
            ▼
          Order
```

---

## 1. MVP users

Include five roles:

| Role | Access |
|------|--------|
| Super Admin | Entire platform/configuration |
| Sales Manager | Campaigns, reports, allocation |
| Lead Operator | Upload/manage leads |
| Sales Executive | Assigned leads, callbacks, survey |
| Service/Technical Executive | Technical/service leads |

Use RBAC from day one.

---

## 2. Lead sources

### A. Daily Excel/CSV upload

Customer uploads leads every day.

Required:

```
Customer_Name
Mobile_Number
Lead_Source
Consent_Status
```

Optional:

```
Lead_ID
Alternate_Number
Interested_Service
Language
City
District
Mandal
Pincode
Monthly_Bill
Monthly_Units
Campaign
Remarks
Consent_Date
Consent_Source
```

Provide a **Download Template** button.

### B. Swaraj Solar website

Connect the current website forms/calculator into:

```
swarajsolar.com
      ↓
Lead API
      ↓
Lead Management
      ↓
AI Campaign
```

Website integration can use webhooks/API so those leads don't require Excel.

---

## 3. Lead validation

Immediately after upload:

```
Upload
 ↓
Validate file
 ↓
Validate phone
 ↓
Normalize +91
 ↓
Duplicate check
 ↓
Previous lead check
 ↓
Consent check
 ↓
DNC/Opt-out check
 ↓
Ready for campaign
```

Show:

```
Uploaded                 2,000
Valid                    1,886
Duplicate                   48
Invalid phone                19
Opted out                    22
Consent issue                25
--------------------------------
Ready                    1,886
```

Rejected leads must have downloadable rejection reasons.

---

## 4. Customer history

Don't call every daily upload as a completely new customer.

Use:

```
CUSTOMER
  │
  ├── Lead #1
  ├── Lead #2
  ├── Previous calls
  ├── Previous survey
  ├── Previous quotation
  ├── Opt-out
  └── Existing installation
```

This prevents embarrassing situations such as AI trying to sell a new system to an existing customer who is calling about maintenance.

---

## 5. Virtual business number

**No physical mobile/SIM is required for the AI architecture.**

For the India deployment, obtain compliant business calling capability from a licensed cloud-telephony provider — not by buying a normal SIM and converting it into a virtual number. A practical starting point is [Exotel](https://exotel.com/) (its virtual business numbers are called ExoPhones); alternatives include Knowlarity, Ozonetel and Airtel IQ. You create a business account, complete the required KYC, obtain/activate a number, and configure it for inbound/outbound calling through their APIs or SIP infrastructure.

Requirements to give the telephony provider (ask for **programmable outbound voice + inbound callback + SIP/media streaming for an AI voicebot + human-agent transfer**, not simply "a virtual number"):

```
Indian business calling
Outbound programmable voice
Inbound callbacks
AI/SIP/media streaming
Call webhooks
Concurrent calls
Call recordings where applicable
Human transfer
Call status
DTMF
Caller ID/business-number configuration
```

Example requirement statement to the provider:

> "Swaraj Solar requires an India business calling number for an AI-based solar lead qualification platform. We require outbound calling to opted-in Indian leads, inbound customer callbacks, concurrent calls, SIP/media integration with our Telugu AI voice agent, call status webhooks, recordings where permitted, and live transfer to sales executives."

Provider setup:

```
Swaraj Solar
    ↓
Business/KYC onboarding
   (GST / company information /
    authorized-person documents)
    ↓
Approved commercial calling configuration
    ↓
Virtual/business number
    ↓
SIP/API
    ↓
Swaraj AI
```

The exact caller-ID/number series must be validated with the provider against current Indian commercial-calling (TRAI) requirements before production. Don't simply choose a random local virtual number and start bulk lead calling.

For the first PoC, start with **one business calling setup**, perhaps 5–10 concurrent calls, and increase channels later. One number per AI agent is not required.

---

## 6. Campaign engine

Manager creates:

```
Campaign Name
Lead List
Service
Language
Start Date
Calling Window
Concurrency
Maximum Attempts
Retry Rules
Priority
```

Actions:

```
CREATE
START
PAUSE
RESUME
STOP
COMPLETE
```

Example:

```
Campaign:
September Residential Leads

Leads:
1,886

Languages:
Telugu + English

Calling:
10 AM – 6 PM

Concurrent calls:
5

Attempts:
Maximum 3
```

Concurrency should be configurable rather than hard-coded.

---

## 7. Call scheduling/retry engine

Example:

```
Attempt #1
    ↓
No Answer
    ↓
Retry later
    ↓
Attempt #2
    ↓
No Answer
    ↓
Next permitted calling period
    ↓
Attempt #3
    ↓
UNREACHABLE
```

Different policies for:

```
NO_ANSWER
BUSY
SWITCHED_OFF
CALLBACK_REQUESTED
FAILED
```

If the customer says:

> "Tomorrow 11 ki call cheyyandi."

that takes precedence over automatic retry rules.

---

## 8. AI introduction

The customer should know they are interacting with an automated assistant.

Example:

> "నమస్తే రమేష్ గారు. నేను Swaraj Solar నుండి మాట్లాడుతున్న AI virtual assistant ని. మీరు solar installation గురించి enquiry ఇచ్చారు. రెండు నిమిషాలు మాట్లాడటానికి ఇది సరైన సమయమా?"

Customer can switch naturally between:

```
Telugu
English
Telugu + English
```

Do not force them to select a language from an IVR if language detection works reliably.

---

## 9. AI voice architecture

```
Customer Audio
      ↓
Speech-to-Text
      ↓
Conversation Orchestrator
      ↓
LLM
      +
Swaraj Knowledge
      +
Qualification State
      +
Business Rules
      ↓
Structured Decision
      ↓
Text-to-Speech
      ↓
Customer
```

For the initial implementation, ElevenLabs can be evaluated for Telugu speech services, while keeping the provider replaceable.

Do **not** build your own foundation LLM.

Build the **Swaraj Solar intelligence layer**.

---

## 10. AI provider abstraction

Create:

```
SpeechProvider
 ├── transcribe()
 └── detect_language()

LLMProvider
 ├── generate()
 └── extract()

VoiceProvider
 └── synthesize()

TelephonyProvider
 ├── call()
 ├── transfer()
 └── hangup()
```

Therefore:

```
ElevenLabs becomes expensive?
       ↓
Change provider

Better Telugu STT appears?
       ↓
Change provider
```

without rebuilding the application.

---

## 11. Service identification

Based on Swaraj Solar's business, the AI should classify into:

```
RESIDENTIAL_SOLAR
PM_SURYA_GHAR
COMMERCIAL_SOLAR
INDUSTRIAL_SOLAR
AGRICULTURE_SOLAR
GROUND_MOUNTED_SOLAR
EXISTING_SOLAR_UPGRADE
SOLAR_MAINTENANCE
PANEL_CLEANING
GENERAL_ENQUIRY
```

Don't restrict the platform to residential/commercial.

---

## 12. Residential workflow

Collect:

```
Customer Name
Location
PIN
Own / Rent
Property Type

Monthly Electricity Bill
Monthly Units

Connection Type
Sanctioned Load

Roof Type
Roof Area
Shadow Availability

Existing Solar

On-grid / Hybrid / Off-grid
Battery Requirement

Expected Installation Date

Finance Interest
Subsidy Interest

Site Survey Interest
Preferred Date/Time
```

The AI shouldn't mechanically ask fields already supplied in the lead.

---

## 13. PM Surya Ghar workflow

Capture:

```
Residential property
Property ownership
Electricity connection
Roof availability

Latest electricity bill
Customer location

Subsidy interest

Required documents/status

Site survey interest
```

Government-scheme eligibility, subsidy values and documentation must come from an approved/current knowledge source rather than LLM memory.

---

## 14. Commercial workflow

```
Company
Contact Person
Designation

Business Type
Location

Own / Lease

Electricity Bill
Monthly Consumption

Sanctioned Load
Contract Demand

Roof/Open Land
Available Area

Existing Solar

Expected Capacity

On-grid / Hybrid / Off-grid

Decision Maker
Budget Status
Timeline

Technical Survey
```

---

## 15. Industrial workflow

Add:

```
Industry
Operating Hours/Shifts

HT/LT
Transformer Capacity
Maximum Demand

Roof Type
Roof Area
Open Land

DG Usage

Existing Solar Capacity
Expansion Requirement

Decision Maker
Timeline
Technical Survey
```

High-value industrial leads should automatically route to a senior executive/engineer.

---

## 16. Agriculture workflow

```
Customer/Farmer
Village
Mandal
District

Land Ownership
Land Area

Agricultural Connection

Pump HP
Water Source

Grid Availability

Solar Pump Requirement
Ground-mounted Requirement

Scheme Interest

Site Survey
```

---

## 17. Maintenance/cleaning workflow

AI should also service existing customers.

```
Existing Customer?
        ↓
Existing System Capacity
        ↓
Installation Date
        ↓
Issue
        │
        ├── Low Generation
        ├── Inverter
        ├── Panel Cleaning
        ├── Wiring
        ├── Monitoring
        ├── Battery
        └── Other
        ↓
Location
        ↓
Urgency
        ↓
Service Ticket
```

---

## 18. Knowledge/RAG

Create a curated Swaraj knowledge base:

```
knowledge/
├── company
├── residential
├── commercial
├── industrial
├── agriculture
├── ground-mounted
├── pm-surya-ghar
├── on-grid
├── hybrid
├── off-grid
├── panels
├── inverters
├── warranty
├── subsidy
├── financing
├── installation
├── net-metering
├── maintenance
└── FAQs
```

Don't blindly index the entire existing website because some pages contain generic/template content.

Only **approved company information** enters production RAG.

---

## 19. Central solar calculation/ROI engine

Create:

```
                  SOLAR ENGINE

Website ROI ──────────┐
                      │
AI Voice Agent ───────┼──→ Solar Calculation API
                      │
Sales Executive ──────┘
```

Inputs can include:

```
Monthly units
Monthly bill
Tariff
Location
Connection
Roof
Available area
System type
```

Outputs:

```
Indicative system size
Roof requirement
Generation estimate
Savings estimate
Project-cost range
Approved subsidy calculation
Net investment
ROI/payback estimate
```

Use engineering-approved formulas.

**The LLM must never calculate these independently.**

---

## 20. Lead scoring

Separate scoring models for each service.

Example residential:

```
Own property               +15
Roof available             +15
Suitable electricity usage +15
Installation <30 days      +20
Site survey                +20
Decision maker             +15
                           ----
                            100
```

Classification:

```
80–100   HOT
50–79    WARM
20–49    COLD
0–19     UNQUALIFIED
```

Weights must be admin-configurable.

---

## 21. AI call output

Every successful call should produce structured data:

```json
{
  "lead_id": "SW-10024",
  "language": "te-IN",
  "service": "RESIDENTIAL_SOLAR",
  "monthly_bill": 7500,
  "monthly_units": 620,
  "property_owned": true,
  "roof_available": true,
  "installation_timeline": "30_days",
  "site_survey": true,
  "lead_score": 92,
  "classification": "HOT",
  "next_action": "SITE_SURVEY"
}
```

And a human-readable summary:

```
Ramesh – Miyapur

Interested in residential rooftop solar.
Own independent house.
Bill approximately ₹7,500/month.
Roof available.
Plans installation within one month.
Requested site survey.

🔥 HOT – 92/100
```

---

## 22. Call dispositions

Use fixed codes:

```
QUALIFIED_HOT
QUALIFIED_WARM
QUALIFIED_COLD

SITE_SURVEY_REQUESTED
CALLBACK_REQUESTED

HUMAN_TRANSFER

NO_ANSWER
BUSY
SWITCHED_OFF

NOT_INTERESTED
WRONG_NUMBER

EXISTING_CUSTOMER

SERVICE_REQUEST

DO_NOT_CALL

AI_ERROR
TELEPHONY_ERROR
```

---

## 23. Human transfer

During a call:

```
AI conversation
       ↓
Customer:
"Sales person tho maatladali."
       ↓
AI detects HUMAN_REQUEST
       ↓
Check executive availability
       ↓
Live Transfer
       ↓
Sales Executive
```

Also transfer/escalate for:

```
Price negotiation
Large commercial customer
Industrial project
Technical engineering question
Complaint
Customer explicitly requests human
AI uncertain
```

If nobody is available:

```
Create PRIORITY CALLBACK
```

---

## 24. Inbound callback

The same business calling setup should support callbacks.

```
AI calls customer
       ↓
Customer misses call

Later customer calls back
       ↓
Business/Virtual Number
       ↓
Swaraj Solar greeting
       ↓
Identify number
       ↓
Retrieve previous lead
       ↓
Continue previous context
```

Then:

```
AI
or
Human sales
```

This is important for production.

---

## 25. Site survey module

Sales/AI creates:

```
Lead
Address
GPS/location if collected with permission
Preferred Date
Preferred Time
Service
Notes
Assigned Engineer
Status
```

Workflow:

```
REQUESTED
   ↓
SCHEDULED
   ↓
ASSIGNED
   ↓
VISITED
   ↓
SURVEY COMPLETED
   ↓
QUOTATION REQUIRED
```

---

## 26. Sales executive portal

Instead of giving executives 500 raw phone numbers:

```
MY PRIORITY LEADS
────────────────────────

🔥 Ramesh – 92
Residential
₹7,500/month
Site survey requested

🔥 ABC Industries – 96
Industrial
₹6 lakh/month bill
Technical visit requested

🟠 Krishna – 67
Residential
Interested after 2 months
```

Actions:

```
Call
Assign
Add Notes
Schedule Survey
Create Follow-up
Mark Won
Mark Lost
```

---

## 27. Manager dashboard

```
SWARAJ SOLAR – TODAY

Uploaded                 2,000
Eligible                 1,886

Attempted                1,600
Connected                  980

Qualified                  530

HOT                        142
WARM                       265
COLD                       123

Site Surveys                76
Human Transfers              9
Callbacks                    83

Residential                 540
PM Surya Ghar               190
Commercial                  104
Industrial                   34
Agriculture                  41
Service                      71
```

---

## 28. Funnel reporting

Track:

```
LEAD
 ↓
CALLED
 ↓
CONNECTED
 ↓
QUALIFIED
 ↓
SITE SURVEY
 ↓
QUOTATION
 ↓
ORDER
 ↓
INSTALLATION
 ↓
COMMISSIONING
```

KPIs:

```
Connection rate
Qualification rate
Site-survey conversion
Quotation conversion
Order conversion
Cost/call
Cost/qualified lead
Cost/site survey
AI minutes
Average call duration
Executive hours saved
Revenue/campaign
Lead source performance
```

---

## 29. Recording/transcript review

Authorized managers should get:

```
Customer
Call time
Duration

▶ Recording

Transcript

AI extracted fields

AI summary

Lead score

Disposition
```

And quality controls:

```
👍 Correct
👎 Incorrect

Wrong transcription
Wrong qualification
Wrong language
Wrong lead score
Bad AI response
```

These reviews should feed the AI-quality process.

---

## 30. Opt-out/DNC

Critical requirement.

Customer:

> "నాకు మళ్లీ call చేయకండి."

Immediately:

```
OPT_OUT = TRUE
       ↓
Suppression List
       ↓
Terminate marketing flow
```

Tomorrow, even if the same number appears in a new Excel:

```
Upload
 ↓
Suppression Check
 ↓
BLOCK
```

No manual dependency.

---

## 31. Compliance/audit

Store:

```
Consent status
Consent source
Consent timestamp
Purpose

Call attempts
Call timestamps

Opt-out
Opt-out timestamp

AI disclosure

Campaign

User actions
```

For production Indian commercial calling, validate the exact number/caller-ID, registration, consent and calling configuration with the selected telecom provider against current TRAI requirements.

---

## 32. Security

MVP should still include:

```
TLS
Encryption at rest
RBAC
MFA for administrators
Secrets Manager
PII masking
Audit logs
Database backup
Retention policy
Recording permissions
API rate limiting
Account lockout
Production access control
```

---

## 33. Final technical stack

| Component | Technology |
|-----------|------------|
| Frontend | Next.js |
| Backend | Python FastAPI |
| Database | PostgreSQL |
| Queue | Redis + Celery |
| Object Storage | Azure Blob/S3 |
| Telephony | India cloud telephony/SIP |
| Telugu STT | Pluggable API |
| AI Brain | Commercial LLM API |
| Telugu TTS | ElevenLabs initially |
| RAG | pgvector |
| Authentication | Entra ID/Auth0 |
| Containers | Docker |
| Infrastructure | Terraform/OpenTofu |
| CI/CD | GitHub Actions/Azure DevOps |
| Observability | OpenTelemetry |
| Metrics | Prometheus/Grafana |
| Logs | Loki |

For MVP: a **modular monolith + workers**, not 15 microservices.

---

## 34. Repository structure

```
swaraj-solar-ai/
│
├── frontend/
│   └── web/
│
├── backend/
│   ├── auth/
│   ├── leads/
│   ├── customers/
│   ├── campaigns/
│   ├── calls/
│   ├── ai/
│   ├── qualification/
│   ├── solar_engine/
│   ├── scoring/
│   ├── surveys/
│   ├── sales/
│   ├── service/
│   ├── reports/
│   └── compliance/
│
├── workers/
│   ├── calls/
│   ├── retries/
│   ├── imports/
│   └── reports/
│
├── knowledge/
│   ├── residential/
│   ├── commercial/
│   ├── industrial/
│   ├── agriculture/
│   ├── subsidy/
│   └── service/
│
├── prompts/
│   ├── telugu/
│   ├── english/
│   └── evaluation/
│
├── database/
│   └── migrations/
│
├── infrastructure/
│   ├── terraform/
│   ├── docker/
│   └── monitoring/
│
└── tests/
    ├── api/
    ├── ai/
    ├── telugu/
    └── integration/
```

---

## 35. Deployment architecture

```
                      INTERNET
                         │
                         ▼
                   Load Balancer
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              Next.js         FastAPI
                                │
                ┌───────────────┼──────────────┐
                ▼               ▼              ▼
           PostgreSQL         Redis       Object Storage
                │               │
                │               ▼
                │          Celery Workers
                │               │
                │               ▼
                │         Telephony API/SIP
                │               │
                │               ▼
                │           CUSTOMER
                │               ↕
                │         Real-time Audio
                │               │
                │               ▼
                │        AI Orchestrator
                │          │    │    │
                │         STT  LLM   TTS
                │          │    │    │
                │          └────┼────┘
                │               │
                └───────────────┘
```

---

## 36. Build sequence

**Sprint 1:** Authentication, DB, customer/lead model, Excel upload, validation, duplicate and opt-out handling.

**Sprint 2:** Campaign engine, call queue, virtual-number/telephony integration and call webhooks.

**Sprint 3:** Real-time AI voice pipeline, Telugu/English STT/TTS, LLM integration and basic conversation.

**Sprint 4:** Residential + PM Surya Ghar workflows and structured extraction.

**Sprint 5:** Commercial, industrial, agriculture, ground-mount and service workflows.

**Sprint 6:** Lead scoring, AI summaries, executive assignment, callback and site-survey modules.

**Sprint 7:** Manager dashboard, reporting, recordings/transcripts and quality review.

**Sprint 8:** Website integration, central ROI API, security hardening, audit/compliance and production pilot.

---

## 37. MVP test strategy

Before calling real customers:

```
100+ scripted AI tests

50+ Telugu conversations

Telangana Telugu
Andhra Telugu
Telugu + English
English

Numbers:
"ఆరు వేలు"
"6000"
"six thousand"

Units
kW
Bills
Locations
Names
PIN codes

Interruptions
Background noise
Customer changing topic
Customer asking human
Customer saying don't call
```

Then run a controlled pilot with **opted-in leads**.

---

## 38. MVP success criteria

Don't judge the project by "AI sounds impressive."

Measure:

```
Telugu understanding accuracy
Required-field capture accuracy
Lead-classification accuracy
AI response latency
Call connection rate
Qualification rate
Human escalation accuracy
Site-survey conversion
Cost per qualified lead
Human calling hours saved
Customer opt-out rate
```

Then compare:

```
Current Human Process
          VS
AI + Human Process
```

That's what establishes the actual ROI.

---

## Final product boundary

At the end of this MVP, Swaraj Solar should be able to do this:

```
9:00 AM
Customer uploads today's 2,000 leads
             ↓
System validates them
             ↓
Manager starts campaign
             ↓
AI automatically calls eligible customers
             ↓
Customer speaks Telugu/English
             ↓
AI identifies required Swaraj service
             ↓
AI qualifies requirement
             ↓
Approved Solar Engine calculates
indicative requirement where applicable
             ↓
AI answers approved FAQs
             ↓
AI scores customer
             ↓
HOT customer requests salesperson
             ↓
Live transfer / priority callback
             ↓
Site survey booked
             ↓
Executive receives complete summary
             ↓
Manager sees entire funnel
             ↓
Customer later calls business number
             ↓
System retrieves previous context
             ↓
AI/Human continues conversation
```

**That is the MVP.**

It is large enough to be a **real deployable Swaraj Solar business product**, while avoiding the expensive mistake of building our own LLM, telecom network or overly complex microservice platform in version 1.
