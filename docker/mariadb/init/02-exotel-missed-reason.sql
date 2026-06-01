-- Preserve the original incoming missed reason on callback rows
ALTER TABLE exotel_outgoing_calls
  ADD COLUMN IF NOT EXISTS missed_reason VARCHAR(100) NULL
  AFTER callback_by_name;
