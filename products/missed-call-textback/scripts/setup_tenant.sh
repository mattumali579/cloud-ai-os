#!/usr/bin/env bash
# Add one shop to schema mctb.
# DATABASE_URL=postgresql://... PUBLIC_BASE_URL=https://host ./scripts/setup_tenant.sh --slug ... 
set -euo pipefail

SLUG=""
BUSINESS_NAME=""
TWILIO_NUMBER=""
OWNER_NAME=""
OWNER_PHONE=""
OWNER_EMAIL=""
HOURS="Mon-Fri 8am-5pm"
BOOKING_LINK=""
TIMEZONE="America/Chicago"
CALL_MODE="forward"
DATABASE_URL="${DATABASE_URL:-}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug) SLUG="$2"; shift 2 ;;
    --business-name) BUSINESS_NAME="$2"; shift 2 ;;
    --twilio-number) TWILIO_NUMBER="$2"; shift 2 ;;
    --owner-name) OWNER_NAME="$2"; shift 2 ;;
    --owner-phone) OWNER_PHONE="$2"; shift 2 ;;
    --owner-email) OWNER_EMAIL="$2"; shift 2 ;;
    --hours) HOURS="$2"; shift 2 ;;
    --booking-link) BOOKING_LINK="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --call-mode) CALL_MODE="$2"; shift 2 ;;
    --database-url) DATABASE_URL="$2"; shift 2 ;;
    --public-base-url) PUBLIC_BASE_URL="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SLUG" || -z "$BUSINESS_NAME" || -z "$TWILIO_NUMBER" || -z "$OWNER_NAME" || -z "$OWNER_PHONE" || -z "$DATABASE_URL" ]]; then
  echo "Required: --slug --business-name --twilio-number --owner-name --owner-phone and DATABASE_URL" >&2
  exit 1
fi

if [[ ! "$SLUG" =~ ^[a-z0-9-]{2,40}$ ]]; then
  echo "Slug must be 2-40 characters of a-z, 0-9, or hyphen." >&2
  exit 1
fi

if [[ "$CALL_MODE" != "forward" && "$CALL_MODE" != "dial" ]]; then
  echo "Call mode must be forward or dial." >&2
  exit 1
fi

normalize() {
  local digits
  digits=$(printf '%s' "$1" | tr -cd '0-9')
  if [[ ${#digits} -eq 10 ]]; then
    printf '+1%s' "$digits"
  elif [[ ${#digits} -eq 11 && ${digits:0:1} == 1 ]]; then
    printf '+%s' "$digits"
  else
    echo "Not a US phone number: $1" >&2
    exit 1
  fi
}

TWILIO_NUMBER=$(normalize "$TWILIO_NUMBER")
OWNER_PHONE=$(normalize "$OWNER_PHONE")
TOKEN=$(openssl rand -hex 24)

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -v slug="$SLUG" \
  -v business_name="$BUSINESS_NAME" \
  -v twilio_number="$TWILIO_NUMBER" \
  -v owner_name="$OWNER_NAME" \
  -v owner_phone="$OWNER_PHONE" \
  -v owner_email="$OWNER_EMAIL" \
  -v hours="$HOURS" \
  -v booking_link="$BOOKING_LINK" \
  -v timezone="$TIMEZONE" \
  -v call_mode="$CALL_MODE" \
  -v token="$TOKEN" <<'SQL'
INSERT INTO mctb.tenants (
  slug, business_name, twilio_number, owner_name, owner_phone, owner_email,
  hours_text, booking_link, timezone, call_mode, dashboard_token
) VALUES (
  :'slug', :'business_name', :'twilio_number', :'owner_name', :'owner_phone', :'owner_email',
  :'hours', :'booking_link', :'timezone', :'call_mode', :'token'
);
SQL

BASE=${PUBLIC_BASE_URL%/}
echo
echo "Shop saved."
echo "Twilio number: $TWILIO_NUMBER"
echo "Owner phone:   $OWNER_PHONE"
echo "Call mode:     $CALL_MODE"
if [[ -n "$BASE" ]]; then
  echo "Voice webhook: $BASE/webhook/mctb-voice"
  echo "SMS webhook:   $BASE/webhook/mctb-sms"
  echo "Dashboard:     $BASE/webhook/mctb-dashboard?token=$TOKEN"
else
  echo "Dashboard token: $TOKEN"
  echo "Dashboard path:  /webhook/mctb-dashboard?token=$TOKEN"
  echo "Set PUBLIC_BASE_URL to print the full links."
fi
echo "Give the owner the dashboard link. It is the password."
