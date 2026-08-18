# ATO source notes, 13 August 2026

Every rule this tool implements, with where it came from. ato.gov.au returns 403 to
most automated fetchers, so these pages were read in a browser on 13 August 2026 and
the figures were cross checked against the published spreadsheet.

## Pages read

| Page | QC | Last updated on the page |
| --- | --- | --- |
| Small business benchmarks | QC 22307 | not stated |
| Types of benchmarks | QC 47934 | 17 March 2025 |
| How we calculate benchmark ratios | QC 37143 | 16 March 2026 |
| Bakeries and hot bread shops | QC 43659 | 16 March 2026 |

Dataset: Small Business Benchmarks, data.gov.au, dataset id
`591b444b-be7f-4121-8252-119a9fe07c19`, licensed CC BY 2.5 AU. The 2023-24 resource
was published 15 March 2026 and is 20,826 bytes, SHA-256
`84925348cc6abfdbfb2cd14c62456e02d44057b7c7b444a6839e6179f71440f5`. The 2022-23
resource is 19,748 bytes, SHA-256
`ecd9ad5169fcdc4ddd5fdf79ceabeee460e950328fa87c8eaa224260e07548c6`.

## Rules implemented

**Turnover.** "All tax return benchmark ratios are expressed as a percentage of
turnover (excluding GST)." Turnover is the amount at the sales of goods and services
label: company 6C, partnership or trust 5G + 5H, individual P8 I + P8 J. "If the
amount reported in these labels is blank, zero, or less than 50% of the amount at the
total business income label, we use the total business income amount instead." Total
business income is company 6S, partnership or trust item 5, individual P8.

Implemented in `ratios.compute`. The 50% test is strict, so sales at exactly half of
total business income remain the turnover figure.

**Total expenses to turnover.** "Total expenses amount is calculated as total
expenses minus the payments to associated persons." Total expenses is company 6Q,
partnership or trust 5O, individual P8S + P8T. Payments to associated persons is
company 8Q, partnership or trust 45 M, individual P16 H.

**Cost of sales to turnover.** "The cost of sales amount excludes salary and wages.
When the Total salary and wages expenses code equals C, meaning that the total salary
and wages expenses are included in the cost of sales figure, we deduct the Total
salary and wages expenses from the Cost of sales." The mapping models this directly
with a separate `cost_of_sales_labour` bucket, which is equivalent and does not
require a tax return code that a profit and loss does not carry.

**Labour to turnover.** "Total salary and wages expenses + Contractor subcontractor
and commissions expenses minus Payments to associated persons. If the amount reported
at label W1 (total salary, wages and other payments) on the activity statement is
greater than the total salary and wages expenses reported on the tax return, then we
use the activity statement amount in the calculation." W1 cannot be seen in a profit
and loss, so it is a `--w1` option and its absence is reported. The return-label
salary and wages figure includes wages to associates and the ATO then deducts them;
the mapping keeps associate payments in their own bucket, so the mapped salary and
wages already exclude them and no further deduction is made. W1 does include them,
so it is compared and applied net of payments to associates.

**Rent and motor vehicle.** Company labels 6H and 6Y respectively.

**Key benchmark range.** From the individual industry page: "Cost of sales to
turnover is the key benchmark range for this industry. It is the most accurate when
predicting business turnover. If you don't report cost of sales, or only report a
small amount, use total expenses to turnover as your key benchmark range instead."
The dataset therefore derives the key ratio as cost of sales where the ATO publishes
one for that industry and total expenses where it does not. Where no cost of sales is
mapped at all, the tool switches to total expenses and says so. The ATO does not put
a figure on "a small amount", so no threshold has been invented; both ranges are
always reported.

**Five ratios exist, two are published in bulk.** Types of benchmarks lists cost of
sales, total expenses, labour, rent and motor vehicle expenses. The data.gov.au
workbook carries the two key ratios only, in columns headed "Total Expenses" and
"Cost of Sales". Labour, rent and motor vehicle ranges appear on the individual
industry pages under "Other benchmarks". They are not in this dataset yet.

**Activity statement benchmarks.** "These benchmarks haven't been produced since
1 July 2017 with the introduction of Simpler BAS." Out of scope.

## Cross check performed

The bakery page publishes, for 2023-24:

| Annual turnover range | $65,000 to $400,000 | $400,001 to $750,000 | More than $750,000 |
| --- | --- | --- | --- |
| Cost of sales divided by annual turnover | 31% to 38% | 34% to 39% | 29% to 36% |
| Total expenses divided by annual turnover | 69% to 81% | 75% to 86% | 82% to 90% |

The shipped dataset holds 0.31 to 0.38, 0.34 to 0.39, 0.29 to 0.36 and 0.69 to 0.81,
0.75 to 0.86, 0.82 to 0.90 for the same industry and the same bands. This is pinned
by `tests/test_dataset.py::test_bakery_matches_the_published_ato_page`.

The same page publishes averages, and labour, rent and motor vehicle ranges, none of
which are in the bulk workbook. That is the gap recorded above.

## Turnover bands

The workbook prints bands as `$65,000 - $400,000`, `$400,001 - $750,000` and
`More than $750,000`. Read literally, turnover of $400,000.50 falls in no band. The
builder therefore sets each band after the first to begin, exclusively, at the
previous band's upper bound, which closes the gap without moving any published
boundary. Four industries publish two bands rather than three, with `N/A` in the high
band column.

## Not checked

- The activity statement ratio section of QC 37143 was not transcribed, since
  activity statement benchmarks have not been produced since 2017.
- The individual industry pages for the other 99 industries were not read. Only the
  bulk workbook and the bakery page were used, so the key ratio derivation rests on
  the rule quoted above rather than on 100 separate confirmations.
- Business industry codes are not mapped to benchmark business types. The ATO lists
  them on each industry page; matching is by name here.
