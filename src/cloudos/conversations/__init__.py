"""BrightReach reply -> memory -> notification -> sales layer.

Sits on top of the lead engine (cloudos.leadgen). Postgres is the memory; this
package never sends email to a prospect. It records confirmed sends, reads
replies, matches them to companies, classifies them in the context of the whole
thread, keeps one authoritative status per company, notifies the owner once per
event, and prepares (never sends) replies, audits and proposals.
"""
