ALTER TABLE hcaller_master
  ADD COLUMN IF NOT EXISTS active TINYINT(1) NOT NULL DEFAULT 1 AFTER caller_status;

UPDATE hcaller_master
SET active = CASE
  WHEN LOWER(TRIM(COALESCE(caller_status, ''))) = 'active' THEN 1
  ELSE 0
END
WHERE active IS NULL OR active NOT IN (0, 1);

ALTER TABLE hcaller_master
  MODIFY caller_code VARCHAR(40) NOT NULL;

ALTER TABLE hhome_collection_booking
  MODIFY booking_code VARCHAR(40) NOT NULL;
