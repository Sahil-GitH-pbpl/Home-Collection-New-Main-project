ALTER TABLE IF EXISTS exotel_incoming_calls
  ADD COLUMN IF NOT EXISTS call_related_to VARCHAR(100) NULL AFTER call_type;
