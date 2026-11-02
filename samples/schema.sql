CREATE TABLE articles (
	article_id INTEGER NOT NULL, 
	headline TEXT NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	article_url VARCHAR(2000) NOT NULL, 
	link_url VARCHAR(2000) NOT NULL, 
	collection_method VARCHAR(60) NOT NULL, 
	listing_url VARCHAR(2000), 
	published_at DATETIME, 
	date_source VARCHAR(20), 
	collected_at DATETIME NOT NULL, 
	excerpt TEXT, 
	article_text TEXT, 
	extraction_status VARCHAR(20), 
	content_hash VARCHAR(64), 
	disease_tags JSON, 
	matched_keywords JSON, 
	relevance VARCHAR(20), 
	content_type VARCHAR(30), 
	event_status VARCHAR(20), 
	relevant_diseases JSON, 
	relevance_reasons JSON, 
	story_id INTEGER, 
	relevance_status VARCHAR(20) NOT NULL, 
	country VARCHAR(100), 
	state VARCHAR(100), 
	district VARCHAR(100), 
	locality VARCHAR(200), 
	latitude FLOAT, 
	longitude FLOAT, 
	location_level VARCHAR(20), 
	location_text TEXT, 
	location_confidence VARCHAR(20), 
	location_review BOOLEAN, 
	location_source VARCHAR(20), 
	location_reasons JSON, 
	location_candidates JSON, 
	PRIMARY KEY (article_id), 
	UNIQUE (article_url)
);

CREATE TABLE collection_runs (
	run_id INTEGER NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	status VARCHAR(20) NOT NULL, 
	config_hash VARCHAR(64), 
	stats JSON, 
	errors JSON, 
	PRIMARY KEY (run_id)
);

CREATE TABLE geocode_cache (
	"query" VARCHAR(500) NOT NULL, 
	result JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY ("query")
);

CREATE TABLE listing_status (
	url VARCHAR(2000) NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	last_ok_at DATETIME, 
	deep_read_at DATETIME, 
	last_error_at DATETIME, 
	last_error TEXT, 
	PRIMARY KEY (url)
);

CREATE TABLE official_reports (
	report_id VARCHAR(60) NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	report_url VARCHAR(2000) NOT NULL, 
	report_year INTEGER NOT NULL, 
	report_week INTEGER NOT NULL, 
	state VARCHAR(100), 
	district VARCHAR(100), 
	disease_raw TEXT, 
	diseases JSON, 
	cases INTEGER, 
	deaths INTEGER, 
	onset_date DATE, 
	reported_date DATE, 
	status VARCHAR(100), 
	action_taken TEXT, 
	latitude FLOAT, 
	longitude FLOAT, 
	location_level VARCHAR(20), 
	location_confidence VARCHAR(20), 
	collected_at DATETIME NOT NULL, 
	PRIMARY KEY (report_id)
);

CREATE TABLE seen_links (
	url VARCHAR(2000) NOT NULL, 
	outcome VARCHAR(20) NOT NULL, 
	attempts INTEGER, 
	first_seen DATETIME NOT NULL, 
	PRIMARY KEY (url)
);
