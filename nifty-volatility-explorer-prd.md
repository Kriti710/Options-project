# Product requirements document
## NIFTY options implied volatility explorer

**Version:** 1.0
**Status:** Draft, pre-build
**Owner:** Kriti Aggarwal
**Last updated:** September 2026

---

## 1. Overview

The NIFTY options implied volatility explorer is a web application that collects live option chain data from the National Stock Exchange of India, derives implied volatility for every listed strike, and presents the resulting volatility smile, term structure, and option Greeks through an interactive interface.

The product exists because option premiums quoted on an exchange do not tell a trader whether an option is expensive or cheap. A premium of ₹120 is meaningless in isolation — it is expensive or cheap only relative to how much the underlying index is expected to move before expiry. That expectation is not published anywhere. It has to be extracted from the price itself by inverting an option pricing model.

This tool performs that extraction, at scale, across the full chain, and renders the result visually. It turns a table of prices into a picture of what the market believes about risk.

---

## 2. Problem statement

Retail options traders in India operate almost entirely on directional views. The publicly available tooling reinforces this: broker platforms show premiums, open interest, and change in open interest, but rarely surface implied volatility in a form that supports comparison across strikes or across time.

The consequences are concrete and expensive. A trader buys options ahead of a scheduled event — a budget announcement, an election result, a monetary policy decision — because they expect a large move. They are frequently right about the direction and still lose money, because implied volatility was inflated going into the event and collapsed immediately after it. The premium they paid contained a volatility component that evaporated on schedule. This phenomenon, commonly called volatility crush, is invisible to anyone who is only watching price.

Separately, the shape of the volatility curve across strikes carries information about where the market perceives tail risk. A steepening downside skew indicates rising demand for crash protection. This signal is available to anyone who computes it and is watched by essentially no retail participant.

The product addresses both gaps by making implied volatility a first-class, visible quantity.

---

## 3. Goals

The product must let a user answer four questions without leaving the interface:

- What is the implied volatility of any given NIFTY option right now?
- What is the shape of the volatility curve across strikes for a chosen expiry, and how does it deviate from the flat line that Black-Scholes assumes?
- How does that curve differ between near-dated and far-dated expiries?
- What are the Greeks — delta, gamma, vega, theta — for a selected contract, and what do they imply about exposure?

A secondary goal is pedagogical. The application should be legible to someone learning derivatives, with concise inline explanation of what each quantity means, so that the tool teaches while it informs.

---

## 4. Non-goals

The product will not execute or simulate trades, will not recommend positions, and will not provide any form of trading signal or advice. It is an analytical instrument, not an advisory service.

It will not attempt to be a live market data terminal. Data will be refreshed periodically rather than streamed tick by tick, for reasons set out in section 7.

It will not model volatility using stochastic volatility frameworks such as Heston or SABR in version 1. Black-Scholes inversion is sufficient to produce the smile, and the interesting insight is precisely that Black-Scholes fails to explain the shape it produces.

It will not cover equity options, currency options, or indices other than NIFTY in version 1.

---

## 5. Target users

**Primary user — the learning derivatives trader.** Someone who understands calls and puts, trades options occasionally or is preparing to, and has heard of implied volatility without being able to use it operationally. They want to see whether options are currently expensive before they buy, and they want to develop intuition for what normal looks like so that abnormal becomes visible.

**Secondary user — the quantitative finance student.** Someone studying derivatives who wants to see textbook theory meet live market data, and specifically wants to observe the volatility smile as an empirical object rather than a diagram in a lecture slide.

**Tertiary user — the technical reviewer.** A recruiter, hiring manager, or interviewer assessing the author's capability. This user does not use the tool functionally; they read the repository, the architecture, and the writeup. Their needs are met by clarity of code and honesty of documentation rather than by features.

---

## 6. Background concepts

**Implied volatility** is the volatility input which, when supplied to the Black-Scholes pricing formula, causes the model's output price to equal the price at which the option is currently trading in the market. Since the other four inputs — spot price, strike, time to expiry, and interest rate — are all observable, volatility is the only unknown, and it can be solved for numerically. Implied volatility is therefore the market's aggregate forecast of future movement, expressed as an annualised percentage.

**The volatility smile** is the curve obtained by plotting implied volatility against strike price for a single expiry. Black-Scholes assumes a single constant volatility, which would produce a horizontal line. Real markets produce a curve, typically elevated at low strikes, because the model's assumption of normally distributed returns understates the frequency and severity of crashes, and traders pay a premium for downside protection accordingly. In Indian index options the curve is usually asymmetric, higher on the put side, and is more accurately called a skew or smirk.

**The volatility surface** extends the smile into a second dimension by plotting implied volatility against both strike and time to expiry.

**The Greeks** are partial derivatives of the option price with respect to its inputs: delta with respect to spot, gamma with respect to delta, vega with respect to volatility, theta with respect to time.

---

## 7. System architecture

The application is separated into three components: a collector, a datastore, and a reader.

**Collector.** A Python script that fetches the NIFTY option chain from NSE, parses it, computes implied volatility and Greeks for every strike, and writes a timestamped snapshot to the datastore. It runs on the author's local machine on a schedule.

**Datastore.** A hosted Postgres instance (Supabase) holding one row per strike per snapshot, with an index supporting retrieval by expiry and snapshot time.

**Reader.** A Streamlit application, deployed to Streamlit Community Cloud, which queries the datastore and renders the interface. The reader never contacts NSE.

This separation is not stylistic. NSE restricts programmatic access: requests to the option chain endpoint return HTTP 401 unless the client first establishes a session against the NSE homepage and presents browser-like headers, requests are throttled to a small number per second, and requests originating from hosting providers are frequently blocked outright by IP. A deployed application cannot reliably fetch this data. Collecting from a residential connection and serving from a database is the only architecture that functions, and it happens to mirror how production market data systems are built.

---

## 8. Functional requirements

**FR-1 — Chain retrieval.** The collector shall retrieve the complete NIFTY option chain for all available expiries, capturing strike, expiry date, option type, last traded price, bid, ask, traded volume, and open interest for each contract.

**FR-2 — Session handling.** The collector shall establish and reuse an authenticated session, presenting headers consistent with a browser client, and shall observe a delay between successive requests sufficient to remain within NSE's rate limits.

**FR-3 — Implied volatility computation.** For every contract passing the data quality rules in section 9, the system shall compute implied volatility by numerically inverting the Black-Scholes formula. The solver shall converge to a tolerance of 1e-6 or report failure explicitly rather than returning an unconverged value.

**FR-4 — Greeks computation.** The system shall compute delta, gamma, vega, and theta for every contract with a valid implied volatility.

**FR-5 — Smile visualisation.** The interface shall plot implied volatility against strike for a user-selected expiry, distinguishing calls from puts, and shall overlay a reference line representing the constant-volatility assumption for comparison.

**FR-6 — Term structure visualisation.** The interface shall allow multiple expiries to be overlaid on a single chart so that differences in curve shape across time to expiry are visible.

**FR-7 — Contract detail.** Selecting an individual strike shall display its price, implied volatility, and full set of Greeks, with a one-line plain-language description of what each Greek measures.

**FR-8 — Historical comparison.** The interface shall allow the user to select an earlier snapshot and view how the volatility curve has changed between the two points in time.

**FR-9 — Data provenance.** Every view shall display the timestamp of the snapshot being shown, so that the user is never misled into believing the data is live.

**FR-10 — Transparency of filtering.** The interface shall indicate how many contracts were excluded by data quality rules and shall make the applied thresholds visible to the user.

---

## 9. Data quality rules

Raw option chain data is unsuitable for direct modelling and must be filtered before implied volatility is computed.

Contracts with zero traded volume shall be excluded, as their quoted prices are stale and produce meaningless volatility values. Contracts whose price falls below a minimum threshold shall be excluded, since the inversion becomes numerically unstable when the premium approaches the tick size. Strikes beyond a configurable distance from the spot price shall be excluded, as deep out-of-the-money contracts exhibit bid-ask spreads wide enough that no single price is representative.

Where both bid and ask are available, the midpoint shall be used in preference to the last traded price, as the last trade may be considerably stale.

Time to expiry shall be computed consistently using a single convention, documented in the repository, since the choice between calendar and trading day counts materially changes the resulting volatility figures.

All exclusions shall be counted and reported rather than silently discarded.

---

## 10. Non-functional requirements

**Performance.** Interface interactions shall respond within two seconds against a snapshot of typical size. Full recomputation of implied volatility across a complete chain shall complete within thirty seconds on the collector.

**Reliability.** Collector failures shall be logged with the failing response, and a failed run shall leave the previous snapshot intact rather than writing partial data.

**Security.** Database credentials shall be supplied through environment configuration and never committed to version control.

**Resilience to upstream change.** NSE alters its response structure without notice. Parsing shall fail loudly with a clear diagnostic rather than silently producing empty or malformed output.

**Compliance.** Data collection shall remain within reasonable request rates and the application shall be presented as an analytical and educational tool, not as a redistributed market data feed.

---

## 11. Out of scope for version 1

User accounts and authentication. Alerting or notifications. Mobile-specific layouts. Indices other than NIFTY. Stochastic volatility models. Three-dimensional surface rendering, which is visually impressive and harder to read than overlaid two-dimensional curves. Backtesting of volatility-based strategies.

---

## 12. Success metrics

The product is successful if a user can determine whether NIFTY options are currently expensive relative to the recent past without consulting any other source, and if the volatility smile is visible and correctly shaped in the deployed application using live collected data.

As a portfolio artefact, it is successful if the repository, deployed application, and writeup together demonstrate not merely an implementation of Black-Scholes but a documented account of where the model disagrees with the market and why.

---

## 13. Risks

**NSE blocks or alters access.** The most likely failure mode. Mitigated by the collector-and-datastore split, by conservative request rates, and by retaining historical snapshots so that the application remains functional even if collection stops entirely.

**Numerical instability in the solver.** Deep in-the-money and far out-of-the-money contracts may fail to converge. Mitigated by explicit convergence reporting and by the filtering rules in section 9.

**Scope expansion.** The natural temptation is to add strategy builders, payoff diagrams, and backtesting. Section 11 exists to be re-read when this happens.

---

## 14. Milestones

Week 1, data retrieval working end to end. Week 2, pricing and inversion validated against known values. Week 3, smile plotted with filtering rules established. Week 4, Greeks, term structure, and persistent storage. Week 5, interface built and deployed. Week 6, documentation and writeup complete.

---

## 15. Open questions

The risk-free rate input has not been decided. Options include a fixed assumption, the current repo rate, or an implied forward rate derived from put-call parity, the last of which is most correct and most work.

Collection frequency has not been decided, and the right answer depends on whether intraday volatility movement is intended to be observable or whether daily snapshots suffice.

Whether the tool should surface an aggregate volatility measure comparable to India VIX, which would give users a single number to track, remains undetermined.

The definition of the primary user in section 5 is inferred from the product's purpose rather than established through conversation with real users, and should be confirmed before feature decisions are made on its basis.
