package com.predictive.monitoring.entity;

import jakarta.persistence.*;
import lombok.*;
import java.time.Instant;
import java.util.List;

@Entity
@Table(name = "sites")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class Site {

    @Id
    @Column(name = "site_id")
    private String siteId;

    @Column(name = "site_name", nullable = false)
    private String siteName;

    @Column(name = "location")
    private String location;

    @Column(name = "created_at")
    private Instant createdAt;

    @OneToMany(mappedBy = "site", fetch = FetchType.LAZY)
    private List<Machine> machines;
}
