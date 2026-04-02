package com.predictive.monitoring.entity;

import jakarta.persistence.*;
import lombok.*;
import java.time.Instant;

@Entity
@Table(name = "machines")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class Machine {

    @Id
    @Column(name = "machine_id")
    private String machineId;

    @Column(name = "site_id")
    private String siteId;

    @Column(name = "machine_name")
    private String machineName;

    /** NORMAL / WARNING / CRITICAL */
    @Column(name = "status")
    private String status;

    @Column(name = "updated_at")
    private Instant updatedAt;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "site_id", insertable = false, updatable = false)
    private Site site;
}
