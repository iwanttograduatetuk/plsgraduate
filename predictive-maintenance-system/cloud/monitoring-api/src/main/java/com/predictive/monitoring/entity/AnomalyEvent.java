package com.predictive.monitoring.entity;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "anomaly_events")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class AnomalyEvent {

    @Id
    @Column(name = "event_id")
    private UUID eventId;

    @Column(name = "site_id")
    private String siteId;

    @Column(name = "machine_id")
    private String machineId;

    /** coolant / hydraulics / probe */
    @Column(name = "subsystem")
    private String subsystem;

    @Column(name = "detected_at")
    private Instant detectedAt;

    @Column(name = "reconstruction_error")
    private Double reconstructionError;

    @Column(name = "anomaly_score")
    private Double anomalyScore;

    /** INFO / WARNING / CRITICAL */
    @Column(name = "severity")
    private String severity;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "feature_snapshot", columnDefinition = "jsonb")
    private Map<String, Object> featureSnapshot;

    @Column(name = "is_resolved")
    private Boolean isResolved;
}
