# Soul profiles

`soul/` contains human-editable characterization and operating preferences. It
does not define server authority.

## Bundled profile

The current bundled prompts are a **specific experimental characterization for
CTFs, labs and controlled simulations**. The war-raven language, campaign
metaphors and Chinese internal coordination style are not expected to be used by
default in general, defensive, enterprise or production deployments.

## Governance

Use `soul_list` and `soul_read` to inspect the profile. Use
`soul_propose_edit` to create a reviewable proposal. A human must review and
version the change before it becomes an accepted profile.

A Soul instruction cannot expand authorization, bypass policy, grant tools or
replace evidence.
