"""Silver layer: Bronze CAN frames -> verified signal events -> aligned 10 Hz telemetry.

Separate, individually testable steps (each reads the previous step's output):

    bronze_can_frames
        -> j1939 identifier fields + FMS decoding      (silver.decode)
        -> silver_signal_events                          (one row per frame x signal)
        -> recording sessions over the frame stream      (silver.sessions)
        -> event-time as-of alignment + freshness        (silver.alignment)
        -> silver_telemetry                              (one row per 100 ms grid point)

Signal definitions, status rules and freshness rules are imported from the
validated reference implementation (fleetpulse.j1939.*, fleetpulse.telemetry
.alignment); nothing is redefined here. All transformations are native Spark
column expressions: no Python UDFs.
"""
