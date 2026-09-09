import io


HEADERS = (
    "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,"
    "City,Monthly_Bill,Interested_Service,Language\n"
)


def _upload(client, headers, csv_text, filename="leads.csv"):
    return client.post(
        "/leads/uploads",
        headers=headers,
        files={"file": (filename, io.BytesIO(csv_text.encode()), "text/csv")},
    )


def test_upload_pipeline_full(client, operator_headers, admin_headers):
    # Pre-register an opt-out so the suppression check has something to hit
    response = client.post(
        "/compliance/opt-outs",
        headers=admin_headers,
        json={"phone": "9111111111", "source": "customer request"},
    )
    assert response.status_code == 201

    csv_text = HEADERS + "\n".join(
        [
            # valid
            "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500,Residential Solar,Telugu",
            # duplicate within file (same number, different format)
            "Ramesh Again,+91 98765 43210,WEBSITE,YES,,,,",
            # invalid phone
            "Broken Phone,12345,EXCEL,YES,,,,",
            # opted out (suppressed above)
            "Opted Out Person,9111111111,EXCEL,YES,,,,",
            # consent issue
            "No Consent,9222222222,EXCEL,NO,,,,",
            # missing required field (no name)
            ",9333333333,EXCEL,YES,,,,",
            # second valid row
            "Krishna,9444444444,EXCEL,OPTED_IN,Kukatpally,3200,PM Surya Ghar,Telugu",
        ]
    ) + "\n"

    response = _upload(client, operator_headers, csv_text)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["status"] == "COMPLETED"
    assert summary["total_rows"] == 7
    assert summary["valid_count"] == 2
    assert summary["duplicate_count"] == 1
    assert summary["invalid_phone_count"] == 1
    assert summary["opted_out_count"] == 1
    assert summary["consent_issue_count"] == 1
    assert summary["missing_field_count"] == 1

    upload_id = summary["id"]

    # Leads created and linked to customers with normalized phones
    response = client.get(f"/leads?upload_id={upload_id}", headers=operator_headers)
    assert response.status_code == 200
    leads = response.json()
    assert len(leads) == 2
    by_name = {lead["customer_name"]: lead for lead in leads}
    ramesh = by_name["Ramesh"]
    assert ramesh["customer_phone"] == "+919876543210"
    assert ramesh["status"] == "READY"
    assert ramesh["lead_ref"].startswith("SW-")
    assert ramesh["interested_service"] == "RESIDENTIAL_SOLAR"
    assert float(ramesh["monthly_bill"]) == 7500.0
    assert by_name["Krishna"]["interested_service"] == "PM_SURYA_GHAR"

    # Rejections downloadable with reasons
    response = client.get(f"/leads/uploads/{upload_id}/rejections", headers=operator_headers)
    assert response.status_code == 200
    reasons = {r["reason"] for r in response.json()}
    assert reasons == {
        "DUPLICATE_IN_FILE",
        "INVALID_PHONE",
        "OPTED_OUT",
        "CONSENT_MISSING",
        "MISSING_REQUIRED_FIELD",
    }

    response = client.get(f"/leads/uploads/{upload_id}/rejections.csv", headers=operator_headers)
    assert response.status_code == 200
    assert "OPTED_OUT" in response.text


def test_reupload_same_number_is_duplicate(client, operator_headers):
    csv_text = HEADERS + "Ramesh,9876543210,WEBSITE,YES,,,,\n"
    response = _upload(client, operator_headers, csv_text)
    assert response.json()["valid_count"] == 1

    # Same number the next day -> previous-lead check flags it
    response = _upload(client, operator_headers, csv_text)
    summary = response.json()
    assert summary["valid_count"] == 0
    assert summary["duplicate_count"] == 1

    # Still only one customer record (customer history, not new identities)
    response = client.get("/customers?phone=9876543210", headers=operator_headers)
    customers = response.json()
    assert len(customers) == 1
    customer_id = customers[0]["id"]
    response = client.get(f"/customers/{customer_id}", headers=operator_headers)
    assert len(response.json()["leads"]) == 1


def test_opt_out_blocks_future_uploads(client, operator_headers, admin_headers):
    csv_text = HEADERS + "Suresh,9555555555,WEBSITE,YES,,,,\n"
    response = _upload(client, operator_headers, csv_text)
    assert response.json()["valid_count"] == 1

    # Customer opts out mid-flight
    response = client.post(
        "/compliance/opt-outs",
        headers=admin_headers,
        json={"phone": "09555555555", "source": "call"},
    )
    assert response.status_code == 201
    assert response.json()["phone"] == "+919555555555"

    # Tomorrow's upload of the same number is blocked with no manual step.
    # (The open lead also makes it a duplicate; opt-out must win over "valid".)
    csv_text = HEADERS + "Suresh,9555555555,EXCEL,YES,,,,\n"
    summary = _upload(client, operator_headers, csv_text).json()
    assert summary["valid_count"] == 0
    assert summary["duplicate_count"] + summary["opted_out_count"] == 1

    # Customer record is flagged
    customers = client.get("/customers?phone=9555555555", headers=operator_headers).json()
    assert customers[0]["opted_out"] is True


def test_upload_rejects_bad_file(client, operator_headers):
    response = _upload(client, operator_headers, "Name,Phone\nRamesh,987\n")
    assert response.status_code == 201
    summary = response.json()
    assert summary["status"] == "FAILED"
    assert "Missing required columns" in summary["error"]

    response = client.post(
        "/leads/uploads",
        headers=operator_headers,
        files={"file": ("leads.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert response.json()["status"] == "FAILED"


def test_xlsx_upload(client, operator_headers):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Customer_Name", "Mobile_Number", "Lead_Source", "Consent_Status", "Monthly_Bill"])
    sheet.append(["Excel Lead", "9666666666", "EXCEL", "YES", 4500])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = client.post(
        "/leads/uploads",
        headers=operator_headers,
        files={
            "file": (
                "leads.xlsx",
                buffer,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["valid_count"] == 1, summary


def test_executive_cannot_upload(client, executive_headers):
    response = _upload(client, executive_headers, HEADERS + "X,9876543210,WEB,YES,,,,\n")
    assert response.status_code == 403


def test_template_download(client, operator_headers):
    response = client.get("/leads/template", headers=operator_headers)
    assert response.status_code == 200
    assert response.text.startswith("Customer_Name,Mobile_Number,Lead_Source,Consent_Status")
