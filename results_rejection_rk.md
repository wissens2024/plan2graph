# Table 5 — Verifier-guided rejection sampling (gated RK ep180)

n=200 · seed=42 · country=0

| Gate | Success | Pass rate | Wilson 95% CI | Expected draws (1/p) | Draws for 95% |
|---|---|---|---|---|---|
| clean (drawable, geometric) | 131/200 | 65.5% | 58.7–71.7% | 1.5 | 3 |
| code: daylight+vent, before repair | 98/200 | 49.0% | 42.2–55.9% | 2.0 | 5 |
| code: daylight+vent, after repair | 177/200 | 88.5% | 83.3–92.2% | 1.1 | 2 |
| code: all rules, before repair | 0/200 | 0.0% | 0.0–1.9% | — | — |
| code: all rules, after repair | 82/200 | 41.0% | 34.4–47.9% | 2.4 | 6 |
| clean ∧ code (all), before repair | 0/200 | 0.0% | 0.0–1.9% | — | — |
| ★ clean ∧ code (all), after repair | 73/200 | 36.5% | 30.1–43.4% | 2.7 | 7 |

★ = hybrid loop accept rate (single draw → accepted compliant drawing). Expected draws = 1/p; repair (A arm) lifts the regulatory gate so geometry becomes binding.
