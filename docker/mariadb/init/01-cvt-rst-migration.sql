-- CVT/RST schema migration

-- Unified contact log JSON for CVT/RST
ALTER TABLE tickets DROP COLUMN IF EXISTS informed_to;
ALTER TABLE tickets DROP COLUMN IF EXISTS cvt_contact_log_json;
ALTER TABLE tickets DROP COLUMN IF EXISTS rst_contact_log_json;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS cvt_rst_contact_log_json JSON NULL;

-- Doctor/Panel JSON snapshot
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS doc_pan_json JSON NULL;

-- Rejected sample fields
ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS sample_type VARCHAR(100) NULL,
  ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(255) NULL;

-- CV tests interpretation column
ALTER TABLE cv_ticket_tests
  ADD COLUMN IF NOT EXISTS interp_text VARCHAR(255) NULL AFTER result_text;

-- Create table if missing
CREATE TABLE IF NOT EXISTS cv_ticket_tests (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticket_id BIGINT UNSIGNED NOT NULL,
    test_name VARCHAR(255) NOT NULL,
    value_text VARCHAR(255) NULL,
    result_text VARCHAR(255) NULL,
    interp_text VARCHAR(255) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cv_tests_ticket (ticket_id),
    CONSTRAINT fk_cv_tests_ticket FOREIGN KEY (ticket_id) REFERENCES tickets(id)
      ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Standardize ticket origin values
ALTER TABLE tickets
  MODIFY ticket_origin ENUM('CCE','ODT','CVT','RST') DEFAULT 'CCE';

UPDATE tickets SET ticket_origin='CVT' WHERE ticket_origin='CV';
UPDATE tickets SET ticket_origin='RST' WHERE ticket_origin='RS';
