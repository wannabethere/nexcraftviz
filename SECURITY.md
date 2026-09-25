# Security

## Reporting a vulnerability

Report privately through GitHub: the repository's **Security** tab →
**Report a vulnerability**. That opens a private advisory only the maintainers
can see. Please do not open a public issue for a vulnerability.

Useful in a report: what an attacker can do, the smallest input that shows it,
the version or commit, and whether a model provider or a host application is
involved. This is a small project maintained best-effort — expect a first reply
in days, not hours.

## What this package does with untrusted input

Worth knowing before you report, and before you deploy it:

- **Model output is data, never code.** A generated chart is a Vega-Lite
  document that is parsed, validated against the data it was given, and
  repaired or refused. Nothing from a model is executed, and no chart is
  rendered from a spec that failed validation.
- **Rows are the caller's.** The package holds no database credentials and
  issues no queries: a caller passes rows in. A question that would need data
  it was not given is declined rather than answered from what is there.
- **Rendering runs locally.** `to_svg` / `to_png` use vl-convert; specs are not
  sent anywhere. Only a configured model provider sees a prompt, and prompts
  carry a *profile* of the data — column names, types, ranges — not the rows,
  except where a caller asks for a table.
- **The HTTP surface is unauthenticated until you set a token.** Set
  `NEXCRAFTVIZ_API_TOKEN` before binding to anything but localhost; `serve`
  warns when you have not. Charts and rows are as sensitive as the data behind
  them.
- **Rendered HTML escapes model text.** A narration containing markup is shown
  as text, not inserted as markup.
