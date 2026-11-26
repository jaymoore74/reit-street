"""IR website fetcher.

Finds and downloads each REIT's quarterly supplemental (and, optionally, earnings
releases and presentations) from its investor relations site.

  models       Link, Discovery and outcome names shared by the modules below
  classify     link text + URL -> document kind and fiscal period
  links        anchor extraction from HTML, with nearby text for period inference
  robots       robots.txt per RFC 9309, cached in ref_web_host
  strategies   one interface, several ways to list documents (static HTML, Q4 feed,
               headless browser, SEC 8-K cross-check)
  fetch        orchestration per company: page check, new-link downloads, check log
  profiles     ir_profiles.csv (versioned) -> ref_ir_profile
  expectations when each company's next supplemental is due; overdue list
  coverage     per-company coverage report
See docs/adr/0009-ir-fetcher.md.
"""
