# Report Validation

Validate a model-generated report:

```bash
agentmon analysis verify-report \
  --model-pack /path/to/model_input_pack \
  --report /path/to/report.md
```

Outputs:

- `report_validation.json`
- `report_validation.md`
- `unsupported_claims.csv.gz`
- `numeric_mismatches.csv.gz`
- `forbidden_claims.csv.gz`

Checks include:

- forbidden conclusion phrases;
- pack-external numbers;
- causal wording without control evidence;
- scheduled/generated token confusion;
- configured/actual concurrency confusion;
- observability failure treated as task failure;
- physical NPU mapping claims when evidence level is insufficient.

Statuses:

- `PASS`: no blocking issue found;
- `PARTIAL`: unsupported claims or numbers require review;
- `FAIL`: forbidden or safety-critical claim found.

