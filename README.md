**ES PPN ID**  
Indonesian bank statement import, automatic payment matching and reconciliation,  
   
 PPN treatment and SPT Masa PPN reporting for ES ERP v15+.  
Sister app to es_gst_au. Same architecture, different tax system, and one  
   
 substantial addition: statement lines are matched against outstanding invoices  
   
 before they are ever coded as income or expense.  
**Why this exists**  
Two problems, one import.  
**Customers paying invoices.** For a services business most incoming lines are  
   
 not expenses to code, they are customers settling invoices. Coding them through  
   
 a rule engine posts the income twice: once when the invoice was raised, once  
   
 when the money arrived. So every line is offered to the matcher first and only  
   
 lines with no plausible invoice reach the rules. A match becomes a Payment  
   
 Entry built by ES ERP own get_payment_entry, which keeps the receivable,  
   
 the party ledger and any linked Payment Request in step, and the bank  
   
 transaction is then reconciled against it.  
**Card and direct expenses.** A card payment is not a credit purchase. The  
   
 document that represents it is a single Journal Entry moving money out of the  
   
 bank into an expense account, splitting out PPN where the treatment calls for  
   
 it. One document per line instead of an invoice, a payment and a matching step.  
**Setup**  
1. **ES PPN Settings** (one per company) — NPWP, PKP status, entity type,  
   
 reporting basis, and the PPN and PPh accounts.  
2. **ES ID Bank Import Settings** (one per company) — matching thresholds,  
   
 amount tolerance, bank charges account, default cost centre.  
3. **ES ID Bank Rule** — coding rules. Nine starter rules ship  **disabled and**  
 **  
 without accounts**, because the right account differs with every chart of  
   
 accounts. Enable and code them before first use.  
**Monthly workflow**  
1. Download the statement CSV from BCA, Mandiri, BNI, BRI or Permata.  
2. Create an **ES ID Bank Import Batch**, attach the file, choose the bank  
   
 account. For BCA also set **Statement Year** — BCA omits the year.  
3. **Parse** — the format is detected, rows classified, duplicates flagged.  
4. **Submit** — Bank Transactions are created.  
5. **Find Payment Matches** — each open line is scored against outstanding  
   
 invoices. Nothing posts; this is safe to re-run while reviewing.  
6. **Post Vouchers** — confident matches become Payment Entries and are  
   
 reconciled; coded lines become Journal Entries; the rest wait for a rule.  
7. At month end, create an **ES PPN Return** for the month and calculate it.  
**How matching decides**  
Scored, not ruled, because no single signal is reliable. An Indonesian transfer  
   
 descriptor usually carries the payer's name, sometimes the invoice number, and  
   
 the amount is often short by the transfer fee.  
| | |  
|-|-|  
| **Signal** | **Score** |   
| Invoice, Payment Request or Sales Order number quoted in the narrative | 60 |   
| Amount equals the outstanding balance exactly | 25 |   
| Amount within the configured tolerance | 12 |   
| Payer name appears in the narrative | 18 |   
| Invoice dated inside the look-back window | 5 |   
| Amount exceeds the outstanding balance | −20 |   
   
Default threshold is 80, so a quoted invoice number plus an exact amount  
   
 settles automatically, while a name plus an exact amount does not.  
Two safeguards are deliberate. A tie at the top score never auto-settles —  
   
 two invoices fitting equally well is a coin toss, and paying down the wrong  
   
 customer's invoice is materially worse than leaving a line unreconciled for a  
   
 day. And a transfer short by up to the tolerance settles the invoice in full  
   
 with the difference posted to bank charges, because a transfer that arrives net  
   
 of the bank's fee is still full settlement, not a customer debt.  
**PPN, and why the rate is modelled as a fraction**  
Since 1 January 2025 the statutory rate is 12 per cent, but most goods and  
   
 services use an "other value" tax base (DPP Nilai Lain) of 11/12 of the selling  
   
 price:  
PPN = 12% × (11/12 × price) = 11% of price  
   
Luxury goods subject to PPnBM use the full price and bear the whole 12 per cent.  
The treatment record therefore stores a statutory rate and a DPP fraction  
   
 rather than a single effective percentage. When the transitional base ends and  
   
 the full price becomes the base, that is a data change on one record; historical  
   
 months keep reporting the rate that actually applied to them.  
Amounts round to whole rupiah, and the base is derived as total − ppn rather  
   
 than total / 1.11, so the two components always re-sum to the bank line.  
faktur_base() returns the 11/12 figure separately, because e-Faktur asks for  
   
 that base and the full 12 per cent applied to it, not the price and 11 per cent.  
   
 The numbers reconcile either way but only one of them matches the form.  
**Shipped treatments**  
| | | | |  
|-|-|-|-|  
| **Treatment** | **PPN** | **SPT line** | **Faktur code** |   
| PPN | 12% on 11/12 base | I.A.2 / II.A | 01 |   
| PPN Barang Mewah | 12% on full base | I.A.2 / II.A | 01 |   
| PPN Dipungut Pemungut | collected by the buyer | I.A.3 / II.A | 02 |   
| PPN Tidak Dipungut | not collected | I.A.4 | 07 |   
| PPN Dibebaskan | exempt | I.A.5 | 08 |   
| Ekspor | zero rated | I.A.1 | — |   
| Non-Objek PPN | outside PPN | I.B | — |   
| PBJT Daerah | regional service tax instead of PPN | I.B | — |   
| PM Tidak Dapat Dikreditkan | input VAT, not creditable | II.B | — |   
| Pribadi | private / non-deductible | II.B | — |   
   
PPN collected by an appointed collector (I.A.3) is excluded from the amount  
   
 payable on the return. The collector remits it; including it would pay the same  
   
 tax twice.  
**Deadlines**  
Under PMK 81/2024 the payment deadline was harmonised to the 15th:  
| | | |  
|-|-|-|  
|   | **Payment** | **Return** |   
| PPN | before filing | end of the following month |   
| PPh 21 / 23 / 4(2) / 25 | 15th of the following month | unified return, 20th |   
   
The return doctype computes all three dates for the period, and a daily  
   
 scheduled task warns Accounts Managers about a closed month with no return.  
   
 A nil month still has to be filed — the late penalty for an SPT Masa PPN is a  
   
 fixed amount per return regardless of how small the month was.  
**Period locking**  
Submitting an ES PPN Return locks its month. Journal Entries, invoices and  
   
 Payment Entries dated inside a filed month are blocked, because changing the  
   
 numbers underneath a filed return puts the ledger out of step with what DJP  
   
 holds — and with the faktur numbers already issued against it. Cancel or amend  
   
 the return to reopen the month.  
**What this app does not do**  
- **It does not file anything.** Coretax and e-Faktur submission need DJP  
   
 credentials and a certificate. The app records faktur numbers and codes  
   
 against invoices so the return reconciles; a human uploads.  
- **It does not allocate faktur serial numbers.** DJP issues the NSFP range.  
- **It does not calculate corporate income tax.** Note that since PP 20/2026  
   
 (effective 22 April 2026) a CV, Firma or ordinary PT can no longer use the  
   
 0.5 per cent final turnover regime; they pay 22 per cent on net profit with  
   
 the Article 31E reduction below Rp 50 billion turnover. ES PPN Settings  
   
 flags this when the entity type is set, but the computation is out of scope.  
- **It does not validate an NPWP beyond its structure.** No checksum is  
   
 published, and claiming to verify more would be false assurance on a field  
   
 that ends up on a faktur.  
**Design notes**  
**Doctype and field prefixes.** Doctypes are named ES ID Bank … and custom  
   
 fields es_id_… / es_ppn_… rather than reusing the es_… names from  
   
 es_gst_au, so both apps can be installed on one site — an Australian company  
   
 with an Indonesian subsidiary — without fighting over the same field.  
**Duplicate protection.** Each statement line gets a SHA-256 fingerprint of  
   
 account, date, description, amounts and running balance, held in a unique field  
   
 on Bank Transaction. Where a bank supplies no balance an occurrence index is  
   
 appended, so two genuinely identical transactions on one day stay two rows  
   
 instead of collapsing into one and quietly losing a payment.  
**Number parsing.** Indonesian exports write 1.386.000,00; some  
   
 internet-banking exports of the same bank write 1,386,000.00. Stripping  
   
 commas as thousands separators, which is safe in Australia, turns 1.386.000,00  
   
 into 1.386 here. The decimal separator is decided per value from which symbol  
   
 appears last.  
**Reporting from events, not balances.** SPT figures are built from taxable  
   
 delivery events, not GL account balances. One PPN account is hit by exports,  
   
 exempt supplies and standard supplies alike, and a balance cannot tell them  
   
 apart — only one of them belongs at I.A.2.  
**Accrual by default.** PPN is due on delivery, not on payment, so accrual is  
   
 the correct basis for a PKP. A cash basis is available and apportions each  
   
 invoice across the payments allocated to it pro rata, so a part-paid invoice  
   
 reports only the paid proportion.  
   
CI runs ruff, compiles every module and validates all doctype and workspace  
   
 JSON on push and pull request. Frappe reads the app straight from the repo, so  
   
 no packaging step is needed — bench get-app <url> is the install path.  
**Licence**  
MIT.  
