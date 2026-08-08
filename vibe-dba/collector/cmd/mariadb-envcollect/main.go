package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strconv"

	"mariadb-collector/internal/collector"
	"mariadb-collector/internal/envcollect"
)

func main() {
	var (
		outDir        string
		mariadbConn   collector.MariaDBConnection
		mariadbClient string
		includeOS     bool
		includeDB     bool
		rds           bool
		packageOpt    collector.OptionalBool
		cleanupOpt    collector.OptionalBool
		verbose       bool
	)
	flag.StringVar(&outDir, "out", "", "output directory; defaults to <hostname>_environment_<date>")
	registerMariaDBConnectionFlags(flag.CommandLine, &mariadbConn)
	flag.StringVar(&mariadbClient, "mariadb-client", collector.FirstAvailable("mariadb", "mysql"), "MariaDB client binary")
	flag.BoolVar(&includeOS, "os", true, "collect operating system environment data")
	flag.BoolVar(&includeDB, "db", true, "collect MariaDB environment data")
	flag.BoolVar(&rds, "rds", os.Getenv("ISRDS") != "", "skip local OS/root-only collection")
	flag.Var(&packageOpt, "package", "package collected data into <output>.tgz after collection")
	flag.Var(&cleanupOpt, "cleanup", "remove collected data directory after collection")
	flag.BoolVar(&verbose, "v", false, "verbose progress logging")
	flag.Parse()

	if outDir == "" {
		outDir = fmt.Sprintf("%s_environment_%s", collector.Hostname(), collector.DateStamp())
	}
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		fatal(err)
	}
	if includeDB {
		if err := collector.EnsureMariaDBConnection(context.Background(), mariadbClient, &mariadbConn, os.Stdin, os.Stderr); err != nil {
			fatal(err)
		}
	} else if err := mariadbConn.ApplyURI(); err != nil {
		fatal(err)
	}

	ctx := &collector.Context{
		Context:          context.Background(),
		Mode:             collector.ModeEnv,
		OutputDir:        outDir,
		MariaDBClient:    mariadbClient,
		MariaDBAdminPath: collector.FirstAvailable("mariadb-admin", "mysqladmin"),
		MariaDBOptions:   mariadbConn.Options(),
		IncludeOS:        includeOS,
		IncludeDB:        includeDB,
		RDS:              rds,
		Verbose:          verbose,
	}

	_ = ctx.WriteFile("collection_start", []byte(collector.Timestamp()+"\n"))
	if err := envcollect.Registry.Run(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "collection completed with errors: %v\n", err)
	}
	_ = ctx.WriteFile("collection_stop", []byte(collector.Timestamp()+"\n"))

	if err := collector.FinalizeCollection(outDir, packageOpt, cleanupOpt, os.Stdin, os.Stderr, os.Stdout); err != nil {
		fatal(err)
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
