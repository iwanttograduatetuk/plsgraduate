package com.predictive.monitoring.entity;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "fault_diagnosis_results")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class FaultDiagnosisResult {

    @Id
    @Column(name = "result_id")
    private UUID resultId;

    @Column(name = "event_id")
    private UUID eventId;

    /** CausReg / CausTR / CausTR+CausReg */
    @Column(name = "model_name")
    private String modelName;

    @Column(name = "diagnosed_at")
    private Instant diagnosedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "root_causes", columnDefinition = "jsonb")
    private List<Map<String, Object>> rootCauses;

    @Column(name = "confidence")
    private Double confidence;
}
