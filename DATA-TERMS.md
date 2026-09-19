# Data terms

The datasets in this repository are derived from public financial disclosure
reports published by the U.S. House of Representatives (Office of the Clerk)
and the U.S. Senate (Office of Public Records via efdsearch.senate.gov).

**These datasets are not covered by the MIT license that applies to the code.**
The underlying reports are public records that this project does not own and
cannot license. Their use is restricted by federal statute, and that
restriction follows the data wherever it goes, including into this repo and
any copy you make of it.

## The statutory restriction

Title I of the Ethics in Government Act of 1978, as amended, 5 U.S.C. app.
§ 105(c) (recodified at 5 U.S.C. § 13107(c)), provides:

> It shall be unlawful for any person to obtain or use a report:
> (A) for any unlawful purpose;
> (B) for any commercial purpose, other than by news and communications media
>     for dissemination to the general public;
> (C) for determining or establishing the credit rating of any individual; or
> (D) for use, directly or indirectly, in the solicitation of money for any
>     political, charitable, or other purpose.

The Attorney General may bring a civil action against any person who obtains
or uses a report for a prohibited purpose, with a civil penalty of up to
$10,000. The Senate's search site requires every user to acknowledge this
text before searching; downloading from this repository does not exempt you
from it.

## What this means in practice

- Personal research, journalism, academic work, and open publication of
  analysis are the intended uses.
- Do not sell this data, bundle it into a paid product, or use it to build
  credit or fundraising lists.
- If you redistribute the data, carry this notice with it.

## Both chambers

The statute above is Title I of the Ethics in Government Act, which covers
every filer under that title, so it applies equally to House and Senate
reports. The two sites differ only in presentation: the Senate search site
requires an acknowledgement before every session, while the House Clerk's
disclosure site (checked 2026-09-19) shows no acknowledgement and no usage
terms of its own. Its "Terms of Service" link points to a table of members'
terms of service in Congress, not to usage terms. The statutory restriction
applies to House reports regardless.

## Processing by third-party model providers

Scanned pages and PDFs are sent to hosted vision models to extract
transaction rows. As of 2026-09-19 the extraction backends are Google's
Gemini API (Gemma 4 and Gemini Flash models). The Gemini API Terms of
Service (effective 2026-03-23) say that for **unpaid** services Google may use
submitted content and responses to improve its products and that human
reviewers may read them, while for **paid** services it does not. Whether the
free Gemma quota inside a billing-enabled project counts as paid is not
stated. This project treats Gemma calls as unpaid for that purpose. The
inputs are public records and the outputs are published here, so the
additional exposure is small, but it is disclosed. Sending reports to a
model for transcription is not a commercial, credit, or solicitation use
under the statute.

This file is a good-faith summary, not legal advice.
