package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"mariadb-collector/internal/collector"
	"mariadb-collector/internal/metrics"
)

func main() {
	var (
		baseDir       string
		name          string
		duration      string
		mariadbConn   collector.MariaDBConnection
		mariadbClient string
		mariadbAdmin  string
		includeOS     bool
		includeDB     bool
		rds           bool
		packageOpt    collector.OptionalBool
		cleanupOpt    collector.OptionalBool
		verbose       bool
	)
	flag.StringVar(&baseDir, "out", ".", "base output directory")
	flag.StringVar(&name, "name", collector.Hostname(), "name used in metrics output directory")
	flag.StringVar(&duration, "duration", "5m", "collection duration: Go duration, minutes as integer, or ctrlc")
	registerMariaDBConnectionFlags(flag.CommandLine, &mariadbConn)
	flag.StringVar(&mariadbClient, "mariadb-client", collector.FirstAvailable("mariadb", "mysql"), "MariaDB client binary")
	flag.StringVar(&mariadbAdmin, "mariadb-admin", collector.FirstAvailable("mariadb-admin", "mysqladmin"), "MariaDB admin binary")
	flag.BoolVar(&includeOS, "os", true, "collect operating system metrics")
	flag.BoolVar(&includeDB, "db", true, "collect MariaDB metrics")
	flag.BoolVar(&rds, "rds", os.Getenv("ISRDS") != "", "skip local OS metrics")
	flag.Var(&packageOpt, "package", "package collected data into <output>.tgz after collection")
	flag.Var(&cleanupOpt, "cleanup", "remove collected data directory after collection")
	flag.BoolVar(&verbose, "v", false, "verbose progress logging")
	flag.Parse()

	runFor, untilSignal, err := parseDuration(duration)
	if err != nil {
		fatal(err)
	}
	outputDir := baseDir + string(os.PathSeparator) + fmt.Sprintf("%s_metrics_%s", name, collector.Timestamp())
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		fatal(err)
	}
	if includeDB {
		if err := collector.EnsureMariaDBConnection(context.Background(), mariadbClient, &mariadbConn, os.Stdin, os.Stderr); err != nil {
			fatal(err)
		}
	} else if err := mariadbConn.ApplyURI(); err != nil {
		fatal(err)
	}

	root, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx := &collector.Context{
		Context:          root,
		Mode:             collector.ModeMetrics,
		OutputDir:        outputDir,
		MariaDBClient:    mariadbClient,
		MariaDBAdminPath: mariadbAdmin,
		MariaDBOptions:   mariadbConn.Options(),
		IncludeOS:        includeOS,
		IncludeDB:        includeDB,
		RDS:              rds,
		Verbose:          verbose,
	}

	_ = ctx.WriteFile("collection_start", []byte(time.Now().Format("2006-01-02 15:04:05")+"\n"))
	if err := metrics.Registry.Run(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "collector startup completed with errors: %v\n", err)
	}
	fmt.Printf("Writing metrics to %s\n", outputDir)

	waitForStop(runFor, untilSignal)
	_ = ctx.WriteFile("collection_stop", []byte(time.Now().Format("2006-01-02 15:04:05")+"\n"))
	cancel()
	time.Sleep(1500 * time.Millisecond)

	if err := collector.FinalizeCollection(outputDir, packageOpt, cleanupOpt, os.Stdin, os.Stderr, os.Stdout); err != nil {
		fatal(err)
	}
}

func parseDuration(value string) (time.Duration, bool, error) {
	if value == "ctrlc" {
		return 0, true, nil
	}
	if minutes, err := strconv.Atoi(value); err == nil {
		return time.Duration(minutes) * time.Minute, false, nil
	}
	d, err := time.ParseDuration(value)
	return d, false, err
}

func waitForStop(runFor time.Duration, untilSignal bool) {
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(sig)

	if untilSignal {
		<-sig
		return
	}
	timer := time.NewTimer(runFor)
	defer timer.Stop()
	select {
	case <-sig:
	case <-timer.C:
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "ERROR:", err)
	os.Exit(1)
}

func registerMariaDBConnectionFlags(fs *flag.FlagSet, conn *collector.MariaDBConnection) {
	fs.StringVar(&conn.URI, "mariadb-conn", os.Getenv("MARIADB_CONN"), "MariaDB connection URI: mariadb://user:pass@host:port/db, mysql://user@host, or socket:/path")
	fs.StringVar(&conn.DefaultsFile, "mariadb-defaults-file", os.Getenv("MARIADB_DEFAULTS_FILE"), "MariaDB defaults file")
	fs.StringVar(&conn.Host, "mariadb-host", os.Getenv("MARIADB_HOST"), "MariaDB host")
	fs.IntVar(&conn.Port, "mariadb-port", envInt("MARIADB_PORT"), "MariaDB TCP port")
	fs.StringVar(&conn.Socket, "mariadb-socket", os.Getenv("MARIADB_SOCKET"), "MariaDB socket path")
	fs.StringVar(&conn.User, "mariadb-user", os.Getenv("MARIADB_USER"), "MariaDB user")
	fs.StringVar(&conn.Password, "mariadb-password", os.Getenv("MARIADB_PASSWORD"), "MariaDB password")
	fs.StringVar(&conn.Database, "mariadb-database", os.Getenv("MARIADB_DATABASE"), "MariaDB default database")
	fs.StringVar(&conn.SSLMode, "mariadb-ssl", os.Getenv("MARIADB_SSL"), "MariaDB SSL mode/value passed to --ssl")
	fs.StringVar(&conn.ExtraOptions, "mariadb-options", os.Getenv("MARIADB_OPTIONS"), "additional raw options passed to mariadb client")
}

func envInt(name string) int {
	value := os.Getenv(name)
	if value == "" {
		return 0
	}
	n, err := strconv.Atoi(value)
	if err != nil {
		return 0
	}
	return n
}
