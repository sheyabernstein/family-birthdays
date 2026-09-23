# accounts — implementation notes

Loaded automatically when Claude Code reads files under `accounts/`.
Deep rationale for this app's own decisions - see the root `AGENTS.md`
for the project-wide orientation these build on.

- **A person record (Person) and a login (Account) are different things,
  on purpose.** Person is per-family ledger data; Account is the one
  global sign-in identity a human has, since the same person can in
  principle appear as a Person in more than one family's ledger (their
  birth family and the one they married into) and should still only ever
  have one login. Contact info is entered on the Person form for
  convenience, but it's actually read from and written to the linked
  Account — see `family/forms.PersonForm` for the find-or-create-and-link
  flow that runs whenever an admin adds contact info to a Person.
  **`PersonForm.clean()` refuses to link an Account that's already linked
  to a Person in a *different* family**, even though that's exactly the
  married-into-two-families case above - the form can't tell "this is
  genuinely the same person, and whoever's adding them here has their
  consent" apart from "this is an arbitrary stranger's real email/phone",
  and the same bug covers both readings: `save()`'s "already linked"
  branch overwrites the shared Account's login email/phone directly, so
  letting the match through would let one family silently corrupt another
  family's real login contact info the next time either one edits it, and
  `family_role` grants instant editor/owner access with no confirmation
  from the account holder either way. Linking the same Account across two
  families for real is still possible, just not self-service through this
  form - it takes an admin/shell action (or the Django admin directly).
  There's no separate invite step for the normal (single-family) case:
  nobody gets an Account without a Person record first - though this is a
  convention `PersonForm` follows, not a DB constraint, so an Account
  created some other way (the Django admin, a one-off shell fix) can still
  end up with zero linked Person rows. That matters beyond just "can't see
  whose it is": `notifications.audience.is_immediate_family` resolves
  *from* the viewer's own linked Person, so an unlinked Account fails
  closed as "not immediate family" for literally everyone, including their
  own parents - a real bug once, not hypothetical. **Editing your own
  contact info once you already have an
  Account is self-service, from My Notifications' Channels card**
  (`accounts.forms.AccountContactForm`, `accounts.views.
  UpdateAccountSettingsView` - both live in `accounts`, not `family`,
  since they only ever touch the `Account` model, with no Person
  provisioning involved) - a different, simpler path than
  `PersonForm`'s: you're editing `request.user` directly, so there's no
  find-or-create-or-link decision to make, and `Account.email`/`phone`'s
  own `unique=True` covers the collision case Person Form has to check
  explicitly. This does *not* cover linking an Account to a Person in
  the first place (the bootstrapping case above) - that still only
  happens through `PersonForm`, owner/editor only.
- **The magic-link sign-in email/SMS is the one message this app sends
  that isn't obviously any one family's** - every other Occurrence/
  Broadcast message has an unambiguous `Message.family` to brand from
  (see the sender-branding bullet above); a sign-in link is requested
  before an account's picked a workspace for this session, and the same
  account can belong to several families at once. `accounts.views.
  _sole_family` (a module-level helper, called from
  `RequestMagicLinkView.post`) resolves this the same way `Message.
  family`'s own exactly-one-of-the-two constraint does elsewhere: brand
  with a family's own name/`sender_email`/`reply_to_email`/
  `sms_sender_id` only when the account belongs to *exactly* one, never
  an arbitrary pick among several - a member of two families seeing one
  of them named on a sign-in email would misrepresent who's actually
  sending it. Zero or multiple memberships both fall back to the same
  blank-everything default `notifications.services.send_email`/
  `send_sms` already use for a message with no family context at all -
  no new fallback path was needed for this. `accounts/templates/accounts/
  email/sign_in.html` is otherwise a normal `templates/email/_base.html`
  extension (see that bullet above) - a call-to-action button (`.btn-
  email-bg`/`.btn-email-text` in the shared base's own `<style>`, kept
  there rather than in this one template since any future call-to-action
  email would want the same button, and its light/dark background+text
  need to invert *together* or the button vanishes against a card
  background that just flipped to match it) with the same raw URL
  repeated underneath in small text, for a client that strips the link
  or someone who'd rather copy-paste. The link's expiry is templated
  from `accounts.magic_links.TOKEN_TTL_SECONDS` (in both the email and
  the SMS body) rather than a hardcoded "15 minutes" - the two used to
  be independent literals with no actual link to the real constant, so
  a TTL change had no way to keep the copy honest.
- **Every phone `<input>` gets the same intl-tel-input widget, wired
  through one shared helper.** `accounts/static/accounts/js/phone_input.js`'s
  `initPhoneInput(selector)` lives under `accounts` (not `family`, even
  though its first call site is `PersonForm.phone`) because every phone
  field on the site is ultimately a front end onto `Account.phone` - see
  the Person/Account bullet below - not a real field on whatever model's
  form happens to render it; `notifications/templates/notifications/
  subscriptions.html`'s own phone field (`accounts.forms.
  AccountContactForm`, editing `Account.phone` directly) uses the exact
  same helper, following the asset-lives-with-its-subject-matter
  convention in "Static files" above. It adds a country-flag picker and
  client-side validation (CDN script/CSS, same pattern as Tom Select/
  Trix), converting the field's visible value to E.164 right before
  submit (`iti.getNumber()`) so `Account.phone`'s own "stored in E.164"
  convention holds with no server-side change needed - the field posts
  the same format the backend already expects regardless of what country
  format the user actually typed in. A blank value is always treated as
  acceptable (every phone field on this site is optional at the
  form-field level; the whole-form "email or phone required" rule, where
  it exists, is enforced elsewhere), so validation only ever fires on
  genuinely typed-but-invalid input, not on an empty field.
  **`static/css/app.css`'s retinting is deliberately scoped to `.iti
  input[type="tel"]`, not added to the shared generic `input[type="..."]`
  selector list the way Tom Select's fields are** - found the hard way:
  the library reserves `padding-left` on the input for its own flag/
  dial-code button (absolutely positioned over the input's left edge),
  and the app's stylesheet loading after the CDN one meant adding
  `input[type="tel"]` there clobbered exactly that property, leaving the
  flag button with nowhere to go and visually colliding with the
  field's own text. The scoped rule sets every other property (font,
  border, background, color, vertical padding) and leaves `padding-left`
  alone on purpose.
- **Every email `<input>` gets the same assistive, server-side validation,
  wired through one shared helper - and it's never authoritative.**
  `accounts/static/accounts/js/email_input.js`'s `initEmailValidation
  (selector, url)` calls `accounts.views.ValidateEmailView` (`POST
  /accounts/validate-email/`, login-required, same "thin AJAX endpoint
  wrapping a pure function" shape as `family.views.GregorianToHebrewView`)
  on blur and again at submit if the value changed since the last check.
  `accounts.helpers.validate_email_address` does three things Django's own
  `EmailField`/`EmailValidator` doesn't: a domain/TLD typo suggestion
  (stdlib `difflib` against a short common-domain/TLD list - a whole-
  domain match wins over a same-domain TLD-only match, since running both
  independently on an uncorrected domain produces a redundant, worse
  second suggestion for the same typo), and a real MX/A-record lookup
  (`dnspython`) that treats a domain as unable to receive mail only when
  it's *definitively* so - genuinely nonexistent (`NXDOMAIN`) or an
  explicit RFC 7505 "null MX" (a lone `.` exchange - `example.com` is a
  real, live instance of this, not hypothetical) - never when the lookup
  merely *fails* (timeout, unreachable nameserver): "couldn't check" is
  always treated as acceptable, so a transient DNS hiccup can't
  manufacture a false rejection. None of this touches the actual save
  path - `PersonForm.email`/`AccountContactForm.email` are still just
  Django's own `EmailField` at submit time, so a network failure here
  degrades to "no extra help," never to "can't save." The JS mirrors that
  same fail-open stance: a failed/errored fetch is treated as acceptable
  rather than invalid. Submission is blocked in the browser only
  (`event.preventDefault()`, re-focusing the field) when the *server*
  returned a definite `valid: false` - this is UX polish, not a second
  authoritative gate, so a request crafted directly against the real
  endpoints is still only ever checked by Django's own validator, exactly
  as before this feature existed.
