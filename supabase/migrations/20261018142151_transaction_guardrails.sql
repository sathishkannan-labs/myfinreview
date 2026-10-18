-- Additive guardrails for the FastAPI transaction service. No financial data is rewritten.
-- The API owns authorization and serializes writes with a lock on public."user".
-- RLS remains enabled; do not grant browser roles direct access to these tables.
ALTER TABLE public.holding
    ADD CONSTRAINT uq_holding_user_symbol UNIQUE (user_id, symbol),
    ADD CONSTRAINT ck_holding_quantity_nonnegative
        CHECK (quantity >= 0 AND quantity < 'Infinity'::double precision),
    ADD CONSTRAINT ck_holding_price_nonnegative
        CHECK (avg_price >= 0 AND avg_price < 'Infinity'::double precision);

ALTER TABLE public."transaction"
    ADD CONSTRAINT ck_transaction_quantity_positive
        CHECK (quantity > 0 AND quantity < 'Infinity'::double precision),
    ADD CONSTRAINT ck_transaction_price_positive
        CHECK (price > 0 AND price < 'Infinity'::double precision);
