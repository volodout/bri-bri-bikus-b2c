ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_status_check;

UPDATE invoices SET status = 'CREATED'    WHERE status = 'PENDING';
UPDATE invoices SET status = 'CANCELLED'  WHERE status = 'REJECTED';

ALTER TABLE invoices
    ADD CONSTRAINT invoices_status_check
    CHECK (status IN ('CREATED', 'PARTIALLY_ACCEPTED', 'ACCEPTED', 'CANCELLED'));
