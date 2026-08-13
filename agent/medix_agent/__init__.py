"""Medix site agent.

Runs at each pharmacy. Exists for two independent reasons, either of
which alone would justify it — see docs/02-architecture.md:

* **Fiscal.** RRA's VSDC is a WAR file on the taxpayer's own local
  webserver. A pure cloud service cannot issue fiscal invoices.
* **Connectivity.** Internet is not guaranteed, and a point of sale that
  stops selling when the connection drops is worthless.
"""

__version__ = "0.1.0"
