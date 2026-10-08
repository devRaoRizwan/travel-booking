# travel.pk notes

Pages read: terms of service, privacy policy, refund policy, auto-booking wallet terms, /trust, /help, route pages. The checkout form is behind login and was not visible.

Relevant points from those pages:
- Names and passport details must match official ID. Name changes after ticketing are restricted.
- A payment receipt is not a ticket. Only the supplier booking reference confirms a purchase.
- Service fees must be disclosed before payment. Refunds follow airline fare rules.
- Payment methods: bank transfer (IBFT/Raast), JazzCash, Easypaisa.
- Ticketing is through Duffel.

## Required before a hold

| Data | Implemented |
|---|---|
| Title, given name, surname as on document; gender; date of birth | yes |
| Nationality; document type, number, issuing country, expiry | yes. International fares require a passport valid 6 months past departure. |
| Contact email and phone | yes |
| Terms version and fare-rules acceptance, with timestamp | yes |
| Itemised price snapshot | yes |

## Questions

1. Does checkout create real supplier holds with a payment deadline? If so, what happens when a manual bank transfer arrives after it?
2. What does checkout collect at hold time versus before ticketing? Is passport data always required up front for international routes?
3. Who reconciles bookings where the airline outcome is unknown, and how fast?
